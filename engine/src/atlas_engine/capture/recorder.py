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
    btn.style.cssText = "position:fixed;top:20px;left:20px;z-index:2147483647;background:#d9534f;color:#fff;border:0;padding:10px 14px;border-radius:10px;font:600 13px system-ui,sans-serif;box-shadow:0 6px 20px rgba(0,0,0,.35);cursor:pointer";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const original = btn.textContent;
      Promise.resolve(window.__atlas_undo_last && window.__atlas_undo_last()).then(() => {
        btn.textContent = "\\u2713 removed";
        setTimeout(() => { btn.textContent = original; }, 1500);
      }).catch(() => {});
    }, true);
    document.body.appendChild(btn);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountUndoButton); else mountUndoButton();
  setInterval(mountUndoButton, 1500);

  const push = (kind, el, value, text, modifiers) => {
    if (!el) return;
    if (el.closest && (el.closest("#__atlasGrabBtn") || el.closest("#__atlasNoteBox") || el.closest("#__atlasUndoBtn"))) return;
    // The extraction button's own label mutates while a grab is in flight
    // ("extracting…" -> "captured" -> reverts after 2.5s), and the note box
    // / undo button mutate their own text the same way ("✓ noted"/
    // "✓ removed") — leaving any of them in the snapshot would make a real
    // action's dom_before/dom_after differ on the overlay alone and be
    // misclassified as dom_changed by the verbatim string-diff classifier in
    // trajectory/extract.py. Clone-and-strip only when at least one is
    // actually present — during authoring replay/brain phases none of them
    // ever are, so this stays free.
    const overlayIds = ["__atlasGrabBtn", "__atlasNoteBox", "__atlasUndoBtn"];
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
      push("click", event.target, null, (event.target.textContent || "").trim().slice(0, 80), mods);
    },
    true,
  );
  document.addEventListener(
    "input",
    (event) => push("fill", event.target, event.target.value),
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
        self.page.expose_binding("__atlas_note", self._on_note)
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

    def _on_note(self, source: dict, text: str) -> None:
        # Attaches to the most-recently-FINALIZED action, same one-event lag
        # dom_after already has (see the class docstring) — the action still
        # _pending at this moment is untouched; there is nothing earlier to
        # attach to if self.actions is still empty.
        if self.actions:
            self.actions[-1].operator_note = text

    def _on_undo_last(self, source: dict) -> None:
        # Same most-recently-FINALIZED semantics as _on_note: the action
        # still _pending at this moment (if any) is untouched.
        if self.actions:
            self._removed.add(self.actions[-1].index)
