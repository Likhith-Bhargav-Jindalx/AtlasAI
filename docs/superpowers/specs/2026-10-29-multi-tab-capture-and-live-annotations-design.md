# Multi-Tab Capture, Live Decision Notes, and Live Step Removal — Design

**Motivation:** operators work across multiple browser tabs simultaneously while filling one form, and today only the tab Atlas started on (`page_index 0`) ever reaches the replayed/certified trajectory — every other tab's actions are captured as orphaned side artifacts (video + raw JSON) that `extract_trajectory` never reads. Separately, recordings carry no record of *why* an action was taken, and a recording mistake today means re-recording the whole flow. Native/desktop-app capture (Windows UIA, macOS AX, Citrix) is explicitly out of scope for this design — browser-only.

## 1. Multi-tab capture → one ordered trajectory

**Current state** (confirmed by reading the code, not assumed):
- `live_capture.py` already runs a `CaptureSession` per tab/popup opened in the context (`_attach_new_pages`), each with its own `RecordedAction` list and wall-clock `timestamp_ms` (`events.py`), but a **local** `index` that restarts at 0 per tab (`recorder.py:120`, `index=len(self.actions)`).
- `recording_service.py:108-126` only runs `extract_trajectory(actions)` on page-0's actions. Every other tab's actions are written to `actions-page{N}.json` and never merged into `trajectory.json`/`ReferenceTrajectory` — the thing the brain loop and golden-script codegen actually use.
- `brain/loop.py` already has `_target_page()` (loop.py:151) resolving a `codegen_expr:pageN....`-prefixed selector to the right `page.context.pages[N]`, and already waits for a popup via `expects_popup`/`context.expect_page()`. This exists only for the codegen-private-channel recorder's popup case today.
- `codegen/emit.py` already has a `_page(page, index)` runtime helper, but **only** reaches it when the step's selector happens to be a `codegen_expr:pageN...` string — a plain-CSS step on a popup page has no page-targeting in the emitted golden script today. This is a real latent bug, fixed as part of this work.

**Changes:**
1. `RecordedAction` (`capture/events.py`) and `TrajectoryStep` (`brain/models.py`) gain `page_index: int = 0`.
2. `recording_service.py`: before calling `extract_trajectory`, merge page-0 `actions` with every `additional_pages[].actions`, sorted by `timestamp_ms`, into one list; re-assign `index` sequentially across the merge (local per-tab indices are discarded, replaced by the merged global order).
3. `trajectory/extract.py::extract_trajectory` stamps `page_index` from each source `RecordedAction` onto its `TrajectoryStep`.
4. `brain/loop.py`'s page targeting generalizes: resolve/act against `page.context.pages[step.page_index]`, waiting for `len(page.context.pages) > step.page_index` before acting on a step whose tab isn't open yet — in addition to (not replacing) the existing `codegen_expr` string-prefix path, which keeps working unchanged.
5. `codegen/emit.py` step emission uses `step.page_index` directly to pick `_page(page, N)`, for every selector kind (plain CSS included) — closes the latent popup/plain-CSS gap above.

**Out of scope:** unifying per-tab video into one continuous recording (stays N separate `.webm` files; `ReviewPage` already renders them per-page); anything about focus switches to windows outside this one Playwright-launched browser context.

## 2. Live decision notes

Operator does the actual recording work in a **separate Playwright-launched browser window** (confirmed via `LiveRecordingPage.tsx`, which is just a timer + Stop button telling the operator to "switch to it, perform the flow"). A note has to be capturable *in that window*, live, not in the Atlas frontend tab.

**Mechanism:** extend the existing injected-overlay pattern (`capture/extraction.py`'s `__atlasGrabBtn`) with a second small floating element — a persistent note box in a corner that doesn't collide with the extract button (now top-right, see objective-4 fix) or common bottom-right portal FABs (e.g. bottom-left).

- New `expose_binding("__atlas_note", ...)` on `CaptureSession`, called when the operator submits text in the note box. Attaches the note to the most-recently-finalized action (the one just appended to `self.actions` by `_finalize_pending` — same "finalized on the next event" lifecycle already used for `dom_after`, nothing new to invent there).
- New field: `RecordedAction.operator_note: str | None` → `TrajectoryStep.operator_note`.
- `repair.py::_build_user_text` includes `operator_note` (when present) as grounding context for repair decisions — this closes the "no action context" gap *and* gives the repair LLM something it doesn't have today.
- The note box's own click/input events are excluded from the action listener exactly like `__atlasGrabBtn` already is (`recorder.py:16`, `el.closest(...)` check) — extended to the new element's id.

## 3. Live step removal (3a)

Bundled into the same overlay as #2, since it's the same injection mechanism and the same `expose_binding` channel.

- Overlay shows a short "recent actions" list (last 5–10, by label/kind) with a remove toggle per entry.
- New `expose_binding("__atlas_flag_remove", ...)` adds an action's (pre-merge, per-tab) index to a `CaptureSession._removed: set[int]`.
- Flagged actions are filtered out of `self.actions` at `__exit__` / before being handed to `persist_recording` — upstream code (the objective-1 merge, `extract_trajectory`) never sees them, so no special-casing needed anywhere else.

**Explicitly deferred (3b, not part of this design):** inserting an arbitrary step at an earlier position, or any post-hoc manual step editor on `ReviewPage`. Live insertion isn't physically meaningful (you can only ever record "now"); true out-of-order insert needs a manual kind/selector/value authoring form and touches the same reindexing surface as #1 (`steps_by_index`, `consumed` sets in `codegen/emit.py::_build_login_block`) plus new API/UI — a separate follow-on design.

## 4. Data model summary

- `RecordedAction`: `+ page_index: int = 0`, `+ operator_note: str | None = None`.
- `TrajectoryStep`: `+ page_index: int = 0`, `+ operator_note: str | None = None`.
- No new tables. No DB migration needed at this stage — both fields live in the blob-stored `actions.json`/`trajectory.json`, not relational columns (matches how every other `TrajectoryStep` field is persisted today).

## 5. Testing plan

- `recording_service.py`: new test asserting multi-tab actions are merged in `timestamp_ms` order with correctly re-assigned `index`/`page_index`.
- `brain/loop.py`: new test replaying a step with `page_index=1` and a plain CSS selector (no `codegen_expr`), confirming it waits for and acts on the right tab.
- `codegen/emit.py`: new golden-emission test for a plain-CSS step on `page_index=1` (covers the latent bug fix).
- `capture/recorder.py`: new tests for `__atlas_note` attaching to the right action, and `__atlas_flag_remove` filtering actions out before they reach `self.actions`/persist.
- `repair.py`: new test confirming `operator_note`, when present, appears in the built prompt text.

## 6. Out of scope (this design)

Native/desktop app capture (Windows UIA, macOS AX), Citrix/remote-streamed apps, unifying multi-tab video into one continuous recording, post-hoc step insertion/editing (3b), any change to what a certified "golden script" output format is for non-web steps (not applicable — everything here stays Playwright/web).
