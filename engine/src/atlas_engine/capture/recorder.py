import time

from atlas_engine.capture.events import RecordedAction

# An IIFE, not a bare arrow function: add_init_script runs its source as
# top-level code (it does NOT auto-invoke a function expression the way
# page.evaluate does), so the script must call itself to actually attach
# listeners on every new document after a navigation.
#
# The URL and DOM are captured here, in JS, and sent along with the event —
# NOT read back afterwards via page.url/page.content() from inside the
# Python binding callback. Playwright's sync API deadlocks if a binding
# callback tries to make a nested synchronous call back into the page.
_LISTENER_SCRIPT = """
(() => {
  // An "undo last step" button, not an arbitrary-index remove list: any
  // JS-side counter tracking WHICH index is "last" would reset on every
  // navigation (a fresh document = a fresh script execution = lost closure
  // state), while Python's self.actions index keeps growing across
  // navigations (it lives on the long-lived CaptureSession, not the page).
  // Almost every real recording navigates at least once, so a stale JS
  // counter would silently flag the WRONG action. Always removing
  // "whatever Python currently considers the last FINALIZED action" sidesteps
  // this entirely — no JS-side state to drift.
  function mountUndoButton() {
    if (document.getElementById("__atlasUndoBtn")) return;
    if (!document.body) { setTimeout(mountUndoButton, 300); return; }
    const btn = document.createElement("button");
    btn.id = "__atlasUndoBtn";
    btn.type = "button";
    btn.textContent = "\\u21b6 Undo last step";
    btn.style.cssText = "position:fixed;bottom:20px;left:20px;z-index:2147483647;background:#d9534f;color:#fff;border:0;padding:10px 14px;border-radius:10px;font:600 13px system-ui,sans-serif;box-shadow:0 6px 20px rgba(0,0,0,.35);cursor:pointer";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const original = btn.textContent;
      Promise.resolve(window.__atlas_undo_last && window.__atlas_undo_last()).then((removed) => {
        btn.textContent = removed ? "\\u2713 removed" : "Nothing to undo";
        setTimeout(() => { btn.textContent = original; }, 1500);
      }).catch(() => {});
    }, true);
    document.body.appendChild(btn);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountUndoButton); else mountUndoButton();
  setInterval(mountUndoButton, 1500);

  // Mandatory per-action note: shown after every click, and once per field
  // (on "change", not "input" -- a real operator's keystrokes each fire
  // their own "input" event, so triggering on that would pop a new modal
  // after every single letter typed). Non-intercepting: the real click/fill
  // already happened and already reached __atlas_record by the time this
  // shows, so a fast navigation can still carry the modal away before the
  // operator sees it -- an accepted tradeoff, not a bug, for not having to
  // preventDefault/replay every click (real risk on a production portal).
  function showPendingNoteModal(defaultText) {
    if (!document.body) return;
    const existing = document.getElementById("__atlasNoteModal");
    if (existing) existing.remove();
    const overlay = document.createElement("div");
    overlay.id = "__atlasNoteModal";
    overlay.style.cssText = "position:fixed;inset:0;z-index:2147483647;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;font:600 13px system-ui,sans-serif";
    const box = document.createElement("div");
    box.style.cssText = "background:#fff;border-radius:12px;padding:16px;box-shadow:0 10px 40px rgba(0,0,0,.3);display:flex;flex-direction:column;gap:8px;min-width:320px";
    const label = document.createElement("div");
    label.textContent = "Why this step?";
    label.style.cssText = "font-weight:700";
    const input = document.createElement("input");
    input.type = "text";
    input.dataset.defaultText = defaultText; // never rendered -- for Use Default and tests only
    input.style.cssText = "padding:8px 10px;border-radius:8px;border:1px solid #ccc;width:100%";
    const btnRow = document.createElement("div");
    btnRow.style.cssText = "display:flex;gap:8px;justify-content:flex-end";
    const defaultBtn = document.createElement("button");
    defaultBtn.id = "__atlasNoteModalDefaultBtn";
    defaultBtn.type = "button";
    defaultBtn.textContent = "Use Default";
    defaultBtn.style.cssText = "background:#eee;color:#333;border:0;padding:8px 12px;border-radius:8px;cursor:pointer";
    const btn = document.createElement("button");
    btn.id = "__atlasNoteModalContinueBtn";
    btn.type = "button";
    btn.textContent = "Continue";
    btn.style.cssText = "background:#2fa84f;color:#fff;border:0;padding:8px 12px;border-radius:8px;cursor:pointer";
    const confirm = (text) => {
      overlay.remove();
      Promise.resolve(window.__atlas_pending_note && window.__atlas_pending_note(text)).catch(() => {});
    };
    defaultBtn.addEventListener("click", () => { input.value = defaultText; input.focus(); }, true);
    btn.addEventListener("click", () => confirm(input.value.trim() || defaultText), true);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") confirm(input.value.trim() || defaultText); }, true);
    btnRow.appendChild(defaultBtn);
    btnRow.appendChild(btn);
    box.appendChild(label);
    box.appendChild(input);
    box.appendChild(btnRow);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    input.focus();
  }

  const push = (kind, el, value, text, modifiers) => {
    if (!el) return;
    if (el.closest && (el.closest("#__atlasGrabBtn") || el.closest("#__atlasNoteModal") || el.closest("#__atlasUndoBtn"))) return;
    // The extraction button's own label mutates while a grab is in flight
    // ("extracting…" -> "captured" -> reverts after 2.5s), and the undo
    // button mutates its own text the same way ("✓ removed") — leaving
    // either in the snapshot would make a real action's dom_before/
    // dom_after differ on the overlay alone and be misclassified as
    // dom_changed by the verbatim string-diff classifier in
    // trajectory/extract.py. Clone-and-strip only when at least one is
    // actually present — during authoring replay/brain phases none of them
    // ever are, so this stays free.
    const overlayIds = ["__atlasGrabBtn", "__atlasNoteModal", "__atlasUndoBtn"];
    const present = overlayIds.some((id) => document.getElementById(id));
    let dom = document.documentElement.outerHTML;
    if (present) {
      const root = document.documentElement.cloneNode(true);
      overlayIds.forEach((id) => {
        const clone = root.querySelector("#" + id);
        if (clone) clone.remove();
      });
      dom = root.outerHTML;
    }
    window.__atlas_record({
      kind,
      value: value ?? null,
      id: el.id || null,
      testId: el.getAttribute ? el.getAttribute("data-testid") : null,
      text: text || null,
      modifiers: modifiers || [],
      url: window.location.href,
      dom,
    });
  };
  document.addEventListener(
    "click",
    (event) => {
      const mods = [];
      if (event.ctrlKey) mods.push("Control");
      if (event.metaKey) mods.push("Meta");
      if (event.shiftKey) mods.push("Shift");
      if (event.altKey) mods.push("Alt");
      const text = (event.target.textContent || "").trim().slice(0, 80);
      push("click", event.target, null, text, mods);
      // A page's own script can click a button programmatically (an
      // auto-advance, a hidden trigger, a restore-draft action) with no
      // human anywhere near it -- isTrusted is false for those, and only a
      // real click should ever prompt the operator for a reason.
      if (!event.isTrusted) return;
      if (event.target.closest && (event.target.closest("#__atlasGrabBtn") || event.target.closest("#__atlasNoteModal") || event.target.closest("#__atlasUndoBtn"))) return;
      // A click into a text field is just focusing it to type next -- not
      // itself a meaningful action. The resulting fill (debounced on
      // "input") gets its own mandatory prompt; forcing one here too
      // would steal focus to the modal before a single letter is typed,
      // as a real recording confirmed: 8 re-clicks of a search box, zero
      // fills, since the operator could never get past this modal to type.
      const tag = event.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      showPendingNoteModal(`Clicked "${text || event.target.id || "element"}"`);
    },
    true,
  );
  // A real operator's keystrokes each fire their own "input" event (see the
  // real multi-keystroke trajectories this session captured), and
  // page.fill() fires exactly one "input" but -- confirmed empirically --
  // NO "change" at all unless something else actually steals focus
  // afterward. Debouncing off "input" itself (reset on every further
  // keystroke on the SAME field) sidesteps that unreliable blur-timing
  // entirely: one modal, ~1200ms after typing in a field last stops --
  // long enough to clear a real mid-word thinking pause -- whether that's
  // from a real pause or the single atomic value fill() sets.
  //
  // The note modal only ever prompts for clicks now -- typing is recorded
  // (push below) but never interrupts the operator with a popup, since a
  // single click can still fire this listener many times (one per
  // keystroke-like input event) and was popping the modal mid-typing.
  document.addEventListener(
    "input",
    (event) => {
      push("fill", event.target, event.target.value);
    },
    true,
  );
})();
"""


def _selector_candidates(record: dict) -> list[dict]:
    candidates = []
    if record.get("testId"):
        candidates.append({"selector": f'[data-testid="{record["testId"]}"]', "rank": 0})
    if record.get("id"):
        candidates.append({"selector": f"#{record['id']}", "rank": 1})
    if not candidates and record.get("text"):
        candidates.append({"selector": f'text={record["text"]}', "rank": 2})
    return candidates


class CaptureSession:
    """Records clicks and fills on a live Page using only public Playwright
    APIs (expose_binding, add_init_script) — never the private
    enableRecorder channel the prototype relied on.

    An action's "after" state (URL, DOM) is only knowable once something
    else happens — including a navigation the action itself triggered — so
    each action is held as `_pending` and only finalized (with an
    after-snapshot) when the *next* event fires, or when the session exits.
    This is why the click that submits a login form correctly ends up with
    dom_after showing the post-login page: that snapshot is taken at
    __exit__, by which point the navigation has completed.
    """

    def __init__(self, page):
        self.page = page
        self.actions: list[RecordedAction] = []
        self._pending: dict | None = None
        self._removed: set[int] = set()
        self.browser_disconnected = False

    def __enter__(self) -> "CaptureSession":
        self.page.expose_binding("__atlas_record", self._on_event)
        self.page.expose_binding("__atlas_pending_note", self._on_pending_note)
        self.page.expose_binding("__atlas_undo_last", self._on_undo_last)
        self.page.add_init_script(_LISTENER_SCRIPT)
        self.page.evaluate(_LISTENER_SCRIPT)  # attach to the already-loaded document too
        return self

    def mark_browser_disconnected(self) -> None:
        """The browser process is gone — e.g. a human closed the window
        before clicking Stop, or it crashed. __exit__ can no longer ask a
        live page for a fresh snapshot; anything already pending gets
        finalized against its own before-state instead of blocking on a
        Playwright call that will never return."""
        self.browser_disconnected = True

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if not self.browser_disconnected:
            try:
                self.page.wait_for_load_state("domcontentloaded")
                self._finalize_pending(url_after=self.page.url, dom_after=self.page.content())
                self._apply_removals()
                return
            except Exception:
                # The browser can still die in the instant between the last
                # liveness probe and this call landing — fall through to the
                # same disconnected finalization below rather than letting a
                # live-page failure here propagate and abort the whole
                # capture session.
                pass
        if self._pending is not None:
            self._finalize_pending(
                url_after=self._pending["url_before"], dom_after=self._pending["dom_before"]
            )
        self._apply_removals()

    def _apply_removals(self) -> None:
        if self._removed:
            self.actions = [a for a in self.actions if a.index not in self._removed]

    def _finalize_pending(self, *, url_after: str, dom_after: str) -> None:
        if self._pending is None:
            return
        pending = self._pending
        self._pending = None
        self.actions.append(
            RecordedAction(
                index=len(self.actions),
                kind=pending["kind"],
                selector_candidates=pending["selector_candidates"],
                value=pending["value"],
                url_before=pending["url_before"],
                url_after=url_after,
                dom_before=pending["dom_before"],
                dom_after=dom_after,
                timestamp_ms=pending["timestamp_ms"],
                modifiers=pending["modifiers"],
                operator_note=pending.get("operator_note"),
            )
        )

    def _on_event(self, source: dict, record: dict) -> None:
        # No calls back into self.page here — everything needed was already
        # captured client-side and handed to us in `record`.
        url_now = record["url"]
        dom_now = record["dom"]

        self._finalize_pending(url_after=url_now, dom_after=dom_now)

        self._pending = {
            "kind": record["kind"],
            "value": record["value"],
            "selector_candidates": _selector_candidates(record),
            "url_before": url_now,
            "dom_before": dom_now,
            "timestamp_ms": int(time.time() * 1000),
            "modifiers": record.get("modifiers") or [],
        }

    def _record_lifecycle_action(self, kind: str, *, value: str | None, url: str, dom: str, tab_origin: str | None = None) -> None:
        """A tab appearing or disappearing is never a DOM event -- nothing in
        _LISTENER_SCRIPT can observe it -- so the caller (live_capture.py,
        watching context.on('page')/page.on('close')) synthesizes it
        directly instead of going through _on_event."""
        self._finalize_pending(url_after=url, dom_after=dom)
        self.actions.append(
            RecordedAction(
                index=len(self.actions), kind=kind, selector_candidates=[], value=value,
                url_before=url, url_after=url, dom_before=dom, dom_after=dom, tab_origin=tab_origin,
            )
        )

    def record_tab_opened(self, url: str, dom: str, *, origin: str) -> None:
        self._record_lifecycle_action("tab_opened", value=url, url=url, dom=dom, tab_origin=origin)

    def record_tab_closed(self, url: str = "", dom: str = "") -> None:
        self._record_lifecycle_action("tab_closed", value=None, url=url, dom=dom)

    def _on_pending_note(self, source: dict, text: str) -> None:
        # Targets the action still _pending — the one the operator was just
        # prompted about by the mandatory modal — not self.actions[-1]
        # (which is always one event behind, per the class docstring). A
        # no-op if nothing is pending (e.g. the modal's binding call races
        # a navigation that already dropped it).
        if self._pending is not None:
            self._pending["operator_note"] = text

    def _on_undo_last(self, source: dict) -> bool:
        # Same most-recently-FINALIZED semantics as _on_note: the action
        # still _pending at this moment (if any) is untouched. Returns
        # whether this click actually removed something new -- nothing is
        # finalized yet, or a repeat click re-targets the same action already
        # in _removed (a set, so re-adding it is a no-op) -- both false, so
        # the button can tell the operator apart from a real removal instead
        # of claiming "removed" every time regardless.
        if not self.actions:
            return False
        index = self.actions[-1].index
        if index in self._removed:
            return False
        self._removed.add(index)
        return True
