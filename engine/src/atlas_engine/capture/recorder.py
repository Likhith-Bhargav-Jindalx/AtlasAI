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
  const push = (kind, el, value) => {
    if (!el) return;
    if (el.closest && el.closest('#__atlasGrabBtn')) return;
    // The extraction button's own label mutates while a grab is in flight
    // ("extracting…" -> "captured" -> reverts after 2.5s), so leaving it in
    // the snapshot would make a real action's dom_before/dom_after differ on
    // the button alone and be misclassified as dom_changed by the verbatim
    // string-diff classifier in trajectory/extract.py. Clone-and-strip only
    // when the button is actually present — during authoring replay/brain
    // phases it never is, so this stays free.
    const btn = document.getElementById('__atlasGrabBtn');
    let dom = document.documentElement.outerHTML;
    if (btn) {
      const root = document.documentElement.cloneNode(true);
      const clone = root.querySelector('#__atlasGrabBtn');
      if (clone) clone.remove();
      dom = root.outerHTML;
    }
    window.__atlas_record({
      kind,
      value: value ?? null,
      id: el.id || null,
      testId: el.getAttribute ? el.getAttribute("data-testid") : null,
      url: window.location.href,
      dom,
    });
  };
  document.addEventListener(
    "click",
    (event) => push("click", event.target, null),
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
        self.browser_disconnected = False

    def __enter__(self) -> "CaptureSession":
        self.page.expose_binding("__atlas_record", self._on_event)
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
        if self.browser_disconnected:
            if self._pending is not None:
                self._finalize_pending(
                    url_after=self._pending["url_before"], dom_after=self._pending["dom_before"]
                )
            return
        self.page.wait_for_load_state("domcontentloaded")
        self._finalize_pending(url_after=self.page.url, dom_after=self.page.content())

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
        }
