# Golden Script Extraction + Download Results — Architecture Design

## 1. Goal

Two related gaps in the deterministic golden script pipeline:

1. **Downloads aren't captured at all in the golden script.** `codegen/emit.py` emits a plain click for a download-triggering step — no wait for the download event, no file saved, nothing returned. (Authoring-time replay/brain *do* capture downloads via `run_step`, just fixed to also catch downloads that fire on a different page than the one clicked — see the eCW cross-page download bug fix landed earlier in this branch.)
2. **"Extract this page" clicks are recorded, then thrown away.** The floating extraction button's click is a real, native-codegen-recorded action, but `capture/codegen.py::strip_extraction_markers` deletes those lines before parsing — so a certified golden script has no idea an extraction ever happened at that point in the flow, and running the script later re-derives nothing.

**Target behavior:** running the deterministic golden script produces a `result` dict containing:
- `extractions`: page data collected at each "Extract this page" point in the recorded flow, keyed by encounter order (`click_1`, `click_2`, ...), each with the URL at that point and the same `{"fields": {...}, "tables": [...]}` shape `capture/extraction.py::collect_page_data` already produces during live capture.
- `downloaded_files`: one entry per download-triggering step, with the saved local filename/path.

That result gets **printed as one line of JSON to stdout** by the script (its only new stdout contract) so a caller can parse it back. The script itself stays fully standalone — no `atlas_engine` import, no DB/blob calls inside it (confirmed: keep it standalone). Persisting extractions to Postgres and uploading downloads to blob storage is the **calling code's** job — currently `authoring/phases.py::run_certify_phase`, the only real caller today.

## 2. New step kind: `extract`

- `TrajectoryStep.kind = "extract"` — no selector needed (extraction runs against the whole current page, not one element); `expected_effect = "none"` (never changes the page).
- `capture/codegen.py::strip_extraction_markers`: instead of deleting the extraction button's click line, replace it with a recognizable marker line (`# __ATLAS_EXTRACT__`), preserving its position in the script.
- `parse_codegen_actions`: recognize that marker and emit `CodegenAction(kind="extract", selector="", ...)`.
- `_TRAJECTORY_KINDS` / `build_codegen_trajectory`: include `"extract"` — it produces a step like any other kind (still excluded: `goto`, per the earlier fix).
- `run_step` / `BrainLoop` (authoring-time replay/brain): when `step.kind == "extract"`, call the existing `capture/extraction.py::collect_page_data(page)` and attach the result to the step's outcome (new `StepOutcome.extracted_data` field) instead of doing a locator-based action at all — no click, no resolve, just run the collector against the current page.

## 3. Golden script emission (`codegen/emit.py`)

- Embed `capture/extraction.py`'s `SERIALIZER` JS as a string constant in the emitted script's header — same pattern as the already-embedded `_page` helper. Fully self-contained; no import needed.
- For an `extract` step, emit code that loops `page.frames` (and `page1.frames`, whichever page is current — same page-root resolution `_emit_locator_call` already does), runs `frame.evaluate(SERIALIZER)`, merges fields/tables across frames (mirrors `collect_page_data`'s Python loop, translated to inline generated code), and appends `{"url": page.url, "data": merged}` into `result["extractions"][f"click_{n}"]` (n = extraction encounter order within the script, 1-based).
- For a step with `expects_download=True`: wrap the action in `with context.expect_event("download", timeout=...) as dli:` (context-scoped, matching the run_step fix — not page-scoped, since some real portals fire the download on a different page than the one clicked). Save the file to a `results/` folder next to the script (`Path(__file__).parent / "results"`), and append `{"filename": ..., "path": ...}` to `result["downloaded_files"]`.
- `run(start_url: str) -> dict` (currently `-> None`): builds up `result = {"fileCount": ..., "filesTransferred": [...], "extractions": {}, "downloaded_files": []}` through the run, returns it, and the `__main__` block does `print(json.dumps(result))` as the script's last line of stdout.

## 4. Consuming the result (`verify/certify.py` + `authoring/phases.py`)

- `certify_golden_script_once`: after a successful run (`returncode == 0`), parse the **last non-empty stdout line** as JSON to recover `result`. A parse failure is tolerated (logged, ignored) — never turns a passing certify run into a failure over a reporting-only concern.
- `run_certify_phase` gains `recording_id: uuid.UUID` (threaded from `_AuthoringRunSession._run`, which already has it) and, **on the first successful run only** (the other `runs - 1` are a determinism check — no need to persist duplicate copies of the same deterministic output):
  - For each `extractions` entry: insert a `PageExtraction` row (`recording_id`, `page_index` = a running counter matching encounter order, `url`, `data`) — reuses the existing model/table; `extracted_at` naturally distinguishes a golden-script-run's extraction from the original capture-time one.
  - For each `downloaded_files` entry: read the local file, upload via `get_store()` to `authoring/<authoring_run_id>/results/<filename>` (matches "results/outcomes folder inside the run's folder").

## 5. Non-goals / explicitly out of scope for this pass

- No change to CAPTURE-time extraction (`PageExtraction` rows created directly from the live recording session) — that keeps working exactly as today.
- No new DB table — reusing `PageExtraction` as-is.
- No wiring for a hypothetical future "production run of a certified script outside authoring" trigger (the barely-used `Run`/`execution.py` model) — this design covers the one real, tested caller today (`run_certify_phase`); a future production-run trigger would follow the same pattern (parse stdout JSON, persist the same way).
- Screenshots/downloads captured during authoring's own replay/brain phases (not the golden script) already got their download-detection bug fixed separately; this spec is only about the deterministic, frozen golden script's own capabilities.

## 6. Files touched

- `capture/codegen.py` — marker preservation instead of deletion, new `extract` action kind, `_TRAJECTORY_KINDS` update.
- `brain/models.py` — `StepOutcome` gains `extracted_data: dict | None`.
- `brain/loop.py` — `run_step`/`_act` handle `kind == "extract"`.
- `codegen/emit.py` — embed `SERIALIZER`, emit extraction + context-scoped download capture, build+print `result`, `run()` returns `dict`.
- `verify/certify.py` — parse stdout JSON from `certify_golden_script_once`.
- `authoring/phases.py` — `run_certify_phase` gains `recording_id`, persists extractions/downloads on first success.
- `authoring/run.py` — thread `recording_id` into the `run_certify_phase` call site.
