# Authoring Pipeline Rebuild: Multi-Pass Authoring with Observability — Design

**Goal:** Replace the current single-pass, headless, black-box `author_from_recording`/`heal_golden_script` flow with a durable, observable, multi-pass pipeline — `replay -> extract -> brain -> golden -> certify` — matching the architecture the user's previous Atlas AI application actually used (verified by reading that codebase directly), which they report got the best accuracy. Also add a page-data-extraction button available during the original recording session, matching that previous app's real feature.

**Non-goal:** Multi-tab/popup trajectory support (the originally-proposed "Stage B") is explicitly deferred until this pipeline rebuild lands. Everything below operates on single-page trajectories only, exactly like the current codebase does today.

## Background: what the previous app actually did (not what was initially assumed)

Direct research against `/Users/apple/Desktop/atlasAI/` (a prior, more mature version of this application, TypeScript/Node backend + Python-generated golden scripts) established the real pipeline, correcting an earlier assumption that a human directly clicks inside the live browser to fix stuck automation steps — **that never existed** in the previous app. The real phases (`frontend/src/lib/constants.ts`, `backend/src/services/PipelineService.ts::runAuthor`):

`capture -> replay -> extract -> brain -> golden -> certify -> done`

- **replay**: deterministic, non-LLM, headed-by-default re-run of the recorded actions, continue-on-failure (a best-effort reference beats zero reference).
- **extract**: builds the reference trajectory from the replay's evidence.
- **brain**: headed, 100% LLM-driven supervised replay — perceive → act → evaluate → on failure, propose one fix from a closed vocabulary, validate the fix against the live DOM before applying it, retry. "Headed" exists so a developer can *watch*, not *act* — the only real human-action points in the old app were a manual-OTP/MFA login pause and a hard-blocker text handoff (CAPTCHA etc.), never a live takeover.
- **golden**: freeze the brain-verified trajectory into a deterministic `.py` script.
- **certify**: headless, the frozen script run twice (default) for stability plus context for a third variant-parameter run; if this fails, the old app loops back into **brain** for another supervised pass (capped repairs) rather than failing outright.

**This project deviates from the old app in one deliberate way, per explicit user direction**: during the `replay` phase, if a step fails, the developer *can* intervene directly in the live headed browser (click/type to unstick it), then click **Resume** to hand control back to automated replay — this matches what the user says actually happened in their own use of the previous app, even though it isn't in the code as written. `brain` stays LLM-only with no human fallback, matching the old app exactly.

## Data model

Four new tables, one status enum extension. All in `engine/src/atlas_engine/db/models/`.

**`AuthoringRun`** (new file `db/models/authoring.py`) — one row per "Author golden" click:
```
id, org_id, recording_id (FK -> recordings), golden_script_id (FK -> golden_scripts, nullable until golden phase completes)
status: running | waiting_for_developer | completed | failed | cancelled
current_phase: replay | extract | brain | golden | certifying | done
repair_attempt: int (0 initially; incremented each time certify failure sends it back to brain)
created_at, ended_at
```

**`AuthoringRunEvent`** — the scrolling log, phase/step-transition granularity (not full verbose DOM/prompt dumps, per user's explicit choice):
```
id, authoring_run_id (FK), timestamp, phase, step_index (nullable), level (info|warning|error), message
```

**`CertificationRun`** — one row per headless certify attempt within a run:
```
id, authoring_run_id (FK), run_number, status (pass|fail), failure_evidence_blob_ref (nullable)
```

**`GoldenScript.status`** gains two values, extending `GOLDEN_SCRIPT_STATUSES` in `db/models/scripts.py`:
```
draft, certified, partially_certified, active, deprecated
```
(`partially_certified` replaces today's binary draft/certified outcome when some but not all certify runs pass; the script is still produced and available, per explicit user direction — this is a genuine, deliberate improvement over the old app's plain boolean.)

**`PageExtraction`** (new, in `db/models/portal.py` alongside `Recording`) — the floating-button feature's output, scoped to the original recording only:
```
id, recording_id (FK -> recordings), page_index (0 = main page), url, data (JSON), extracted_at
```
Kept as a list on the `Recording` (survives regardless of how many authoring runs happen afterward), per explicit user direction.

## Backend: orchestration

`AuthoringRun` is driven by a dedicated background thread — same pattern as `live_capture.py`'s `_LiveCaptureSession`: one thread owns every Playwright call for the run's lifetime, communicates with the API layer only through DB rows (status/phase/events) and a `threading.Event`-based stop/resume signal, never touched from another thread.

**`POST /recordings/{id}/author`** creates the `AuthoringRun` row (`status=running, phase=replay`) and starts the thread, returning the run id immediately — this replaces today's synchronous `author_from_recording` call.

### Phase 1 — `replay`

Opens a **headed** browser, runs each of the recording's original trajectory steps via the existing `run_step` (`brain/loop.py`) — no LLM involved. On success, logs an `AuthoringRunEvent` and advances. On failure (exception or `on_track=False`):
1. Logs the failure.
2. Sets `AuthoringRun.status = waiting_for_developer`.
3. Attaches the same kind of capture listeners `CaptureSession` uses (`capture/recorder.py`) to the live page so whatever the developer does gets recorded as `RecordedAction`s.
4. Blocks until `POST /authoring-runs/{id}/resume` is called.
5. Splices the captured developer actions into the trajectory in place of the failed step, sets `status` back to `running`, and continues automated replay from the next original step.

If the developer can't unstick a step (they'd use Stop, not a special "give up" control, per the earlier-established Stop semantics below), the run ends `failed` with no golden script. Output: a completed, evidence-backed trajectory.

### Phase 2 — `extract`

Builds the `ReferenceTrajectory` (`brain/models.py`) from Phase 1's replay evidence — same shape/logic as today's `extract_trajectory`, just fed the replay-completed action list instead of the raw capture's.

### Phase 3 — `brain`

Opens a **fresh headed** browser and runs the existing `BrainLoop` (`brain/loop.py`) with `llm` set, unchanged in its internal repair logic (cache lookup → LLM `think()` → validated `replace_selector`/`force_click`, else leave off-track). The only changes: it runs headed (not headless, so a developer can watch), and every step/repair emits an `AuthoringRunEvent`. A step that's still off-track after `_repair` hard-fails the run (`status=failed`) — no developer fallback here, matching the old app exactly.

### Phase 4 — `golden`

`emit_golden_script` (`codegen/emit.py`), unchanged. The generated script text is stored and becomes visible in the UI as soon as this phase completes.

### Phase 5 — `certifying`

`certify_golden_script` (`verify/certify.py`), run 3 times by default (configurable), each attempt recorded as a `CertificationRun` row. If not every run passes:
- `AuthoringRun.repair_attempt` increments; if under the cap (default 2, matching the old app), the run goes **back to Phase 3 (`brain`)** for a fresh supervised pass over the same Phase-2 trajectory, then re-emits golden and re-certifies.
- Once repairs are exhausted, the `GoldenScript.status` is set from the **last** certify attempt: all-passed → `certified`; some-passed → `partially_certified`; the script is produced either way.

`AuthoringRun.status` becomes `completed` once a final status is reached (certified or partially_certified) or `failed` if `replay`/`brain` themselves never produced a usable trajectory.

### Stop

`POST /authoring-runs/{id}/stop` cancels the run immediately regardless of phase — closes whatever browser is open, sets `status=cancelled`, no golden script is produced. Matches the earlier-established "abandon the whole attempt" semantics exactly.

## Page-data-extraction button (capture phase only)

Reuses the previous app's actual injected JS verbatim (it's plain JS handed to Playwright, portable as-is): a fixed bottom-right button (not draggable — correcting the user's recollection) injected via `context.add_init_script(BUTTON)` + `context.expose_binding(...)`, wired onto the main page and every popup via the same `context.on("page", ...)` mechanism Stage A already added to `live_capture.py`. On click, a DOM-walking `SERIALIZER` (also ported verbatim) builds structured JSON — headings, tables (as header/row pairs), and labeled fields (from `data-lbl`/`aria-label`/form labels) — and the result becomes one `PageExtraction` row. Available **only** during the original operations-agent recording session (`live_capture.py`), not during `replay`/`brain`, per explicit user direction. The developer's Review/Authoring page lists every `PageExtraction` for the recording alongside the golden script content; downloading one is just fetching its stored JSON.

## API surface (new/changed)

- `POST /recordings/{id}/author` — now starts an `AuthoringRun` and returns `{authoring_run_id}` instead of blocking for a result.
- `GET /authoring-runs/{id}` — polled every ~1.5s by the frontend; returns `{status, current_phase, repair_attempt, events: [...], golden_script: {...} | null, certification_runs: [...]}`.
- `POST /authoring-runs/{id}/resume` — only valid when `status=waiting_for_developer`.
- `POST /authoring-runs/{id}/stop` — valid any time before `completed`/`failed`/`cancelled`.
- `GET /recordings/{id}/extractions` — list of `PageExtraction` rows for the developer's Review/Authoring page.

## Frontend

`AuthoringPage` gains: a phase tracker (`replay → brain → certifying → done`, with a "repair attempt N" indicator when looping back), a scrolling event log (phase/step transitions per the confirmed detail level), a **Resume** button shown only when `status=waiting_for_developer`, a **Stop** button while running, a code viewer for the generated script's current text (appears after `golden`, updates again after any repair loop), and a list of the recording's `PageExtraction`s with a download action per entry. Same polling pattern (`react-query`) already used elsewhere in this app — no new push infrastructure, per explicit user direction.

## Testing / manual verification

Unit/integration tests follow the existing patterns (`test_recording_service.py`-style DB-backed tests, `test_live_capture.py`-style real-headed-browser tests against the fixture portal). Manual verification: run a full authoring cycle against the fixture portal, including deliberately breaking a selector between recording and authoring to exercise the `replay`-phase developer-intervention path, and confirm the extraction button produces a `PageExtraction` during the original recording.

## Explicitly flagged simplifications (for review)

1. **Variant-parameter certification run** (the old app's 3rd certify run using deliberately different input values, e.g. a shifted date) is **not** included — this project's certify phase runs the same script 3× unchanged, since the current app has no concept of parameterized golden-script inputs to vary. Flag if this matters.
2. **MFA/manual-login pause** from the old app's `replay` phase is not carried over — the current app has no credential/login modeling beyond replaying recorded fill/click steps, so login just replays like any other step.
3. Certify's repair-loop cap and the number of certify runs both default to the old app's values (2 repairs, ~3 certify runs) but are simple constants, not configurable per-org — flag if per-org tuning is needed.
