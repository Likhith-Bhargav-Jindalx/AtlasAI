# Golden Script Emission Parity — Architecture Design

Supersedes `2026-08-15-golden-script-extraction-and-downloads-design.md` (not yet
implemented) — that spec covered only §6/§7 below in isolation and got the
extraction architecture wrong (see §6). This is the complete picture, based on a
full, line-by-line read of the previous app's four emission files (pyEmit.ts,
pyLoginCodegen.ts, pyStepCodegen.ts, pyHelpers/printCapture.ts).

## 0. Why this is bigger than it first looked

The user's ask was "match the previous app's generated script exactly." A
line-by-line comparison shows the current `codegen/emit.py` is missing most of
what made the previous app's output production-usable, not just differently
styled. Two of these gaps are real, reproducible bugs in the current app today:

1. **Real credentials are hardcoded into the generated `.py` file** (the actual
   password, in plaintext, in a script that may get committed/shared/archived).
   The previous app never bakes a secret in — it reads `os.environ["SECRET_..."]`
   at runtime.
2. **A session-random iframe name is hardcoded** (`iframe[name="i1F008FA...`).
   That GUID-shaped name is generated fresh per BI-viewer session — the exact
   same class of bug as `_looks_session_random` already guards against for
   plain element IDs elsewhere in this codebase. The previous app never bakes
   in an iframe name at all; it re-resolves the frame **by content**, every
   step, every run.

## 1. Secret & parameter handling

- A recorded fill value is one of three things at emit time:
  - **A secret** (username/password/security-answer, identified via the
    existing `SecretsRef` table's `secret_key_ref`) → never baked in. Emitted
    as `os.environ.get("SECRET_<KEY_REF_UPPER>", "")`, read into `params` at
    the top of `main()`.
  - **PHI** (the step's recorded value is a real patient-identifying value
    the recording happened to use, e.g. a real patient's DOB) → never baked in
    either. Emitted as a `--<kebab-name>` CLI arg with `default=""`, plus a
    `# TODO: no captured/parameterized value` comment and a header-banner
    warning line (`N fill step(s) have no captured/parameterized value (PHI
    redaction): <ids>`).
  - **A normal parameter** (e.g. a date range, a search term) → emitted as a
    `--<kebab-name>` CLI arg with `default=<captured value>` — still
    overridable, but safe to ship with the recorded default.
- `PortalProfile`/`Recording` need a way to flag which recorded values are
  secrets vs. PHI vs. plain params. Today nothing distinguishes them — this is
  new, required plumbing, not just an emission change.

## 2. Login flow emission

Emit a dedicated `login(page, params)` / `attempt_login_once(page, params)` /
`dismiss_post_login_continue(page)` set of functions (mirroring
`pyLoginCodegen.ts` almost verbatim — this session already faithfully ported
the "click Next to reveal password" adaptive piece, everything else below is
new):

- `login()`: retries `attempt_login_once` up to 3 times, logging each retry's
  prior error; after a successful attempt, waits (up to 60s) for the portal's
  `successSignal` locator (already inferred and stored on `PortalProfile`,
  currently unused for this purpose) to confirm login actually landed, not
  just "no exception was thrown." Raises a clear final error naming the
  successSignal if it never appears after 3 attempts.
- `attempt_login_once()`: navigate (`networkidle`, `load` fallback) → fill
  username → adaptive password-reveal (already ported) → fill password →
  submit → `wait_for_timeout(5000)`.
- `dismiss_post_login_continue()`: idempotent loop (bounded to 10 iterations)
  dismissing any configured post-login interstitial.
- **Explicitly out of scope for this pass**: MFA variants (manual-pause,
  email-IMAP-poll, HTTP-OTP-API-poll, Graph-poll) and the security-question
  lockout-safe handler. These exist in the previous app but nothing in this
  codebase's `PortalProfile`/capture flow currently configures or detects
  them — building unused code paths isn't justified. Flagged here so it's a
  deliberate deferral, not an oversight.

## 3. Per-step emission: try/except + diagnostics + selector fallback chains

- Every step (except ones that already intentionally swallow failure, e.g. a
  `wait_for`-only step) gets wrapped:
  ```python
  try:
      <preWait>
      <action>
  except Exception as e:
      dump_failure_context(<frame or page>, page, <step index>, <label>, str(e), diagnostics_dir)
      raise
  ```
- `dump_failure_context`: on any step failure, writes `step_N_failure.png`
  (screenshot) and `step_N_failure.json` (`{stepId, label, error, url, html,
  screenshot}`, raw/unsanitized, local-only — matches the previous app's own
  documented boundary: a separate layer sanitizes before anything reaches an
  LLM, not this function).
- **Multi-fallback selector chains**: each step already carries ranked
  `selector_candidates` (this codebase's existing concept, already used by
  `_resolve_locator` at authoring time) — emit them as nested try/except,
  first candidate tried, falling through to the next on failure, matching
  `nestFallback`'s pattern. This reuses data already captured; no new capture
  work needed, only new emission logic.

## 4. Frame resolution: always dynamic, never a baked-in name

- Replace the current `iframe[name="..."]`-literal emission with a
  `find_frame_with_element(pg, selector, timeout_ms, host=None,
  prefer_child=False)` helper embedded in every generated script (ported
  verbatim from the previous app — this session's `brain/loop.py` doesn't
  need this helper since replay/brain already resolve frames live via
  Playwright's own frame tree at runtime; this is specifically for the
  **frozen, standalone** script, which has no such live resolution today).
- Called **fresh, every step** that targets a nested frame — never hoisted
  into a variable reused across steps (the previous app's comment explains
  why: a later step's element can live in a deeper/different iframe than an
  earlier step's, so a cached frame reference goes stale).
- `host=` comes from the step's own recorded frame label (a URL substring
  hint, e.g. `"reportframe"`), extracted the same way this codebase's replay
  path already does it — never the iframe's own `name`/`id` attribute, which
  is exactly the value that turned out to be session-random for eCW's BI
  viewer.

## 5. `window.print()` → PDF capture

Ported verbatim (`printCapture.ts`, 105 lines, no design changes needed):
`context.add_init_script` overrides `window.print` in every frame to
serialize the DOM (styles + innerHTML + base href + title) and notify Python
via an exposed binding (no native print dialog, which Playwright can't drive)
→ stash the snapshot → at run end, render each stashed snapshot to a real PDF
in a throwaway headless browser, named `<row-prefix><sanitized-title>[_N].pdf`.
Entirely inert when `window.print()` is never called by the portal.

## 6. Extraction — **design correction, not a straight port**

The earlier spec assumed "extraction" means porting `capture/extraction.py`'s
`SERIALIZER` (the live, floating-button, whole-page heuristic DOM-walker) into
the golden script. **The previous app does not do this at all.** Its
extraction is a *compiled specification* — exact `{name, selector, read}`
field entries and `{name, rowSelector, columns}` table entries, decided once
at authoring time — replayed at runtime as direct, targeted Playwright reads
(`_extract_<name>(scope)` functions), not a live DOM walk.

**Recommendation: do NOT match this piece literally.** A frozen selector spec
is exactly the class of brittleness this session has spent most of its time
fixing elsewhere (session-random IDs, hardcoded iframe names, stale selectors
after portal changes) — baking in "read exactly this selector" at authoring
time reintroduces that same fragility for extraction specifically. The
DOM-walking heuristic (already built, already used at capture time, and
inherently self-adapting to minor DOM changes) is the more robust choice for
a healthcare-portal automation tool, even though it diverges from the
previous app here. Keeping the original spec's approach for extraction only
(§2 of the superseded spec): embed `SERIALIZER` in the golden script, run it
across frames at each `extract` step, accumulate into `result["extractions"]`
keyed by encounter order. Flagging this explicitly as a deliberate,
recommended deviation rather than silently picking one approach — open to
being overruled if exact parity matters more here than it looks like it
should.

## 7. Downloads — same "don't revert a fixed bug" flag

The previous app's download emission (`emitDownloadAction`) scopes the wait to
`page.expect_download(...)` — **page-scoped**, not context-scoped. This is
the exact pattern that caused the eCW cross-page download bug this session
already found and fixed (in `run_step`, for authoring-time replay/brain).
**Recommendation: keep the context-scoped fix** (`context.expect_event
("download", ...)`) in the golden script emission too, rather than
reproducing the previous app's narrower (and, per this session's own live
eCW evidence, buggy-in-practice) scoping. Same flag as §6 — a deliberate,
recommended deviation from literal parity, not an oversight.

## 8. Batch mode

`--batch <path>` (a JSON file: a list of `{inputName: value}` row dicts).
Login runs once; each subsequent row re-navigates to the URL captured right
after login succeeded, waits, then re-runs the flow with that row's params
merged in. A row that raises is caught and recorded
(`{"fileCount": 0, "filesTransferred": [], "extractions": {}, "error": str(e)[:400], "inputs": row}`)
rather than aborting the whole batch. `_atlas_row_prefix(row)` builds a
filename-safe prefix from the row's values so per-row downloads/PDFs/reports
never collide. Final shape: `{"batch": [row_result, ...]}`.

## 9. Result / stdout contract

- `run_flow()` always returns `{"fileCount": N, "filesTransferred": [...]}`,
  plus `"extractions": {...}` **only when non-empty** (byte-for-byte
  unchanged output when the flow has no extract steps — same "absent = no-op"
  discipline this codebase already uses everywhere else, e.g. the pacing
  feature).
- **Exactly one `print(json.dumps(result))`**, at the very end of `main()`.
  Nothing else ever goes to stdout — all logging (`log_step`, print-capture
  status) goes to stderr exclusively. A caller can safely parse the entirety
  of stdout as one JSON blob (no "find the last line" heuristics needed).
- Batch mode wraps into `{"batch": [...]}`.

## 10. CLI / argparse surface

One `--<kebab-case>` flag per parameterized fill (§1), plus `--headless`
(default True) / `--headed` (`store_false`, same dest), `--diagnostics-dir`
(default `"diagnostics"`), `--batch` (default `""`). `main()` builds `params`
from these plus the secret env vars, and (if `--batch` given) loads the batch
JSON into `params["_batch"]`.

## 11. Header / metadata banner

```
# ==============================================================================
# GENERATED by Atlas AI Stage 5 (Python target) — portal: <name>  mode: <mode>  platform: <platform|-`
# Source steps.json confidence: <confidence>  needsHumanReview: <bool>
#
# REVIEW BEFORE TRUSTING RESULTS:
#  - N step(s) marked FRAGILE (no durable selector): <ids>                          [only if any]
#  - N fill step(s) have no captured/parameterized value (PHI redaction): <ids>      [only if any]
#  - N frame-finder(s) degraded to a "body" anchor — verify these resolve correctly. [only if any]
#  - This flow opens at least one new page/popup — the trigger step is a judgment call, not a certainty. [only if any]
# ==============================================================================
```
Computed live at emit time from a pass over the trajectory's steps — no new
stored state needed beyond what's already on `TrajectoryStep`/`PortalProfile`
(confidence score already exists on `PortalProfile.confidence_score`).

## 12. Consuming the result (supersedes §4 of the earlier spec)

Same as before: `certify_golden_script_once` parses stdout as JSON (now
provably the *entire* stdout, not just the last line — see §9). On the first
successful certify run only, `run_certify_phase` (gains `recording_id`,
threaded from `_AuthoringRunSession._run`) persists `extractions` into
`PageExtraction` rows and uploads `filesTransferred`/`downloaded_files` via
`get_store()` under `authoring/<run_id>/results/`.

## 13. Explicitly out of scope for this pass

- MFA/security-question login variants (§2) — nothing configures them today.
- Literal parity on extraction architecture (§6) and download scoping (§7) —
  recommended deviations, flagged for override.
- `pyHealEmit.ts`'s interactive interpreter — that maps to this codebase's
  existing `BrainLoop`, a different (already-built) thing entirely, not a
  frozen-script concern.
- A production-run trigger outside authoring's certify phase (the barely-used
  `Run`/`execution.py` model) — same deferral as the earlier spec.

## 14. Files touched (expanded from the earlier spec)

- `db/models/portal.py` / a migration — a way to mark a recorded value as
  secret vs. PHI vs. plain param (new column(s) on `TrajectoryStep`'s stored
  form, or a side table — TBD in the implementation plan).
- `capture/codegen.py` — extract-marker preservation (unchanged from earlier
  spec), no changes needed for secret/PHI detection (that's a capture-time
  concern, separate from codegen parsing).
- `brain/models.py` — `StepOutcome.extracted_data` (unchanged from earlier spec).
- `brain/loop.py` — `extract` step handling (unchanged from earlier spec).
- `codegen/emit.py` — the largest change: header banner, secret/param
  argparse generation, `login`/`attempt_login_once`/`dismiss_post_login_continue`
  emission, per-step try/except + `dump_failure_context` + fallback chains,
  `find_frame_with_element` (replacing baked-in iframe names), print-capture
  init script + PDF flush, batch-mode `run()` wrapper, extraction (§6,
  SERIALIZER-based) and download (§7, context-scoped) result accumulation,
  `main()` with full argparse surface and the single `print(json.dumps(...))`.
- `verify/certify.py` — parse stdout JSON (unchanged from earlier spec).
- `authoring/phases.py` / `authoring/run.py` — thread `recording_id`
  (unchanged from earlier spec).
