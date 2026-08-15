# Golden Script Emission Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `codegen/emit.py`'s generated golden script up to parity with the previous app's output — secrets/PHI never hardcoded, retry-wrapped and verified login, per-step failure diagnostics with selector fallback chains, always-dynamic iframe resolution (fixing a real hardcoded-session-random-iframe-name bug), `window.print()`→PDF capture, batch mode, and a real `result`/stdout contract — while deliberately keeping two already-fixed improvements over the previous app (DOM-walking extraction instead of a frozen selector spec; context-scoped download capture instead of page-scoped).

**Architecture:** `codegen/emit.py` grows from one flat per-step loop into distinct emission passes — review header, secret/param classification and argparse, a login block driven by the trajectory's own username/password steps, the main per-step sequence (now with try/except + fallback chains + dynamic frame resolution), print-capture wiring, and a `main()` that assembles CLI parsing, single-run or batch execution, and the one `print(json.dumps(result))` contract. `capture/codegen.py` gains a new `extract` step kind. `brain/loop.py` gains extract-step handling for authoring-time replay/brain. `verify/certify.py` and the authoring orchestrator (`authoring/phases.py`/`authoring/run.py`) consume the script's stdout JSON and persist extractions/downloads.

**Tech Stack:** Python, Playwright sync API, pytest, SQLAlchemy (existing `GoldenScript.input_schema`/`output_schema` JSON columns — no new migration needed for parameter/secret metadata).

**Spec:** `docs/superpowers/specs/2026-08-15-golden-script-emission-parity-design.md` (supersedes `docs/superpowers/specs/2026-08-15-golden-script-extraction-and-downloads-design.md`)

## Global Constraints

- **Corrected during preflight review (see ledger): the structural machinery this plan adds — the review header banner, per-step try/except diagnostics, dynamic frame resolution, print-capture wiring, and the `--headless`/`--diagnostics-dir`/`--batch` argparse surface plus the JSON result contract — is ALWAYS present in the emitted script, regardless of whether the specific trajectory has a fill/download/extract step. That's the point of this plan: every golden script gets this structure, matching the previous app. Only what's genuinely conditional on the trajectory's content stays conditional:**
  - The `login`/`attempt_login_once`/`dismiss_post_login_continue` block and any `SECRET_*` env var reads — emitted only when the trajectory has a password step (Task 4's `_build_login_block` returns `("", set())` otherwise).
  - Per-fill `--<kebab-name>` CLI flags — one per classified `CliParam`; a trajectory with zero fill steps gets zero of these (still gets `--headless`/`--headed`/`--diagnostics-dir`/`--batch`, which are unconditional).
  - `result["extractions"]` — only present in the printed JSON when at least one extract step ran (existing "absent = no-op" discipline still applies here specifically).
  - Pacing (`_pace`/`PACING_MS`) — unchanged from the existing feature, still opt-in per `pre_action_ms`/`settle_after_nav_ms`.
- The generated script stays fully standalone — **no `atlas_engine` import inside the emitted script**. Only `re`, `sys`, `os`, `json`, `argparse`, `time`, and `playwright.sync_api` may be imported by generated code.
- Keep the context-scoped download fix (`context.expect_event("download", ...)`) — do NOT revert to the previous app's page-scoped `page.expect_download()`.
- Keep the DOM-walking `SERIALIZER`-based extraction — do NOT build the previous app's frozen-selector-spec extraction architecture.
- Every new emission helper needs its own unit test asserting on the generated **text** (matching this codebase's existing `test_codegen_emit.py` style: assert substrings appear/don't appear, then `compile()` the result for syntax validity) — do not skip to only integration-level testing.
- Follow this codebase's established secret-scrubbing pattern from `brain/evaluate.py`'s `_rematerialize`/`_REDACTED_RE` for inspiration on the *shape* of a marker, but do NOT modify `TrajectoryStep`'s dataclass or replay/brain's runtime value handling — secret/param classification for emission is a separate, parallel concern computed at golden-phase time, not baked into the trajectory itself.

---

### Task 1: Secret/param classification (`codegen/params.py`)

**Files:**
- Create: `src/atlas_engine/codegen/params.py`
- Test: `tests/test_codegen_params.py`

**Interfaces:**
- Consumes: `atlas_engine.brain.models.ReferenceTrajectory`/`TrajectoryStep` (existing).
- Produces: `classify_step_values(trajectory: ReferenceTrajectory, portal_domain: str) -> ClassifiedParams`, where:
  ```python
  @dataclass(frozen=True)
  class SecretParam:
      step_index: int
      label: str        # "username" | "password"
      env_var: str       # e.g. "SECRET_TXBSIRAPP_ECWCLOUD_COM_USERNAME"

  @dataclass(frozen=True)
  class CliParam:
      step_index: int
      cli_name: str      # kebab-case, e.g. "start-date"
      default_value: str

  @dataclass(frozen=True)
  class ClassifiedParams:
      secrets: tuple[SecretParam, ...]
      params: tuple[CliParam, ...]
  ```
  Later tasks (3, 4, 11) import `classify_step_values`, `SecretParam`, `CliParam`, `ClassifiedParams` from `atlas_engine.codegen.params`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_codegen_params.py
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.codegen.params import classify_step_values


def test_password_step_becomes_a_secret_with_a_domain_derived_env_var():
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
        TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    secrets_by_label = {s.label: s for s in result.secrets}
    assert secrets_by_label["password"].step_index == 1
    assert secrets_by_label["password"].env_var == "SECRET_PORTAL_EXAMPLE_COM_PASSWORD"
    assert secrets_by_label["username"].step_index == 0
    assert secrets_by_label["username"].env_var == "SECRET_PORTAL_EXAMPLE_COM_USERNAME"


def test_username_is_only_classified_when_it_immediately_precedes_the_password_fill():
    """A fill that happens well before the password step (e.g. a search box
    used earlier in a multi-page login) is NOT assumed to be the username —
    only the fill immediately preceding the password fill is."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#search", "rank": 0}], "foo", "none"),
        TrajectoryStep(1, "click", [{"selector": "#next", "rank": 0}], None, "none"),
        TrajectoryStep(2, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    labels = {s.step_index: s.label for s in result.secrets}
    assert labels == {2: "password"}  # step 0 (a click sits between it and
    # the password fill) is NOT auto-classified as username


def test_every_other_fill_becomes_a_kebab_case_cli_param_with_the_recorded_default():
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
        TrajectoryStep(1, "fill", [{"selector": "#startDate", "rank": 0}], "01/01/2026", "none"),
        TrajectoryStep(2, "fill", [{"selector": "#endDate", "rank": 0}], "07/07/2026", "none"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    params_by_index = {p.step_index: p for p in result.params}
    assert params_by_index[1].cli_name == "start-date"
    assert params_by_index[1].default_value == "01/01/2026"
    assert params_by_index[2].cli_name == "end-date"
    assert params_by_index[2].default_value == "07/07/2026"
    assert 0 not in params_by_index  # the password step is a secret, not a param


def test_no_password_step_means_no_secrets_at_all():
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#continue", "rank": 0}], None, "none"),
        TrajectoryStep(1, "fill", [{"selector": "#search", "rank": 0}], "foo", "none"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    assert result.secrets == ()
    params_by_index = {p.step_index: p for p in result.params}
    assert params_by_index[1].cli_name == "search"


def test_cli_name_derivation_strips_non_alphanumerics_and_camel_case():
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#reportStartDate2", "rank": 0}], "x", "none"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    assert result.params[0].cli_name == "report-start-date2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_codegen_params.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atlas_engine.codegen.params'`

- [ ] **Step 3: Write the implementation**

```python
# src/atlas_engine/codegen/params.py
import re
from dataclasses import dataclass

from atlas_engine.brain.models import ReferenceTrajectory


@dataclass(frozen=True)
class SecretParam:
    step_index: int
    label: str
    env_var: str


@dataclass(frozen=True)
class CliParam:
    step_index: int
    cli_name: str
    default_value: str


@dataclass(frozen=True)
class ClassifiedParams:
    secrets: tuple[SecretParam, ...]
    params: tuple[CliParam, ...]


def _is_password_step(step) -> bool:
    """Same heuristic portal_profile/infer.py::_is_password_action already
    uses to find the login step — password fields are near-universally
    implemented with an id/name/type containing "password"."""
    return step.kind == "fill" and any(
        "password" in c["selector"].lower() for c in step.selector_candidates
    )


def _env_var(portal_domain: str, label: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", portal_domain).strip("_").upper()
    return f"SECRET_{sanitized}_{label.upper()}"


def _cli_name(step) -> str:
    """Derives a kebab-case CLI flag name from a step's selector — strips
    the leading '#'/'.', splits camelCase, replaces non-alphanumerics."""
    raw = step.selector_candidates[0]["selector"] if step.selector_candidates else f"field{step.index}"
    raw = raw.lstrip("#.")
    # Split camelCase boundaries (reportStartDate2 -> report Start Date2)
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", raw)
    kebab = re.sub(r"[^A-Za-z0-9]+", "-", spaced).strip("-").lower()
    return kebab or f"field-{step.index}"


def classify_step_values(trajectory: ReferenceTrajectory, portal_domain: str) -> ClassifiedParams:
    """Decides, for every fill step in the trajectory, whether its recorded
    value should be (a) a secret — never baked into the generated script,
    read from an env var at runtime — or (b) a plain CLI parameter — baked
    in as an overridable default. Exactly one fill step (the recorded
    password field) is ever classified "password"; the fill immediately
    preceding it (no other step in between) is classified "username". Every
    other fill step becomes a CLI param."""
    steps = list(trajectory.steps)
    password_index = next((i for i, s in enumerate(steps) if _is_password_step(s)), None)

    secrets: list[SecretParam] = []
    secret_indices: set[int] = set()
    if password_index is not None:
        password_step = steps[password_index]
        secrets.append(SecretParam(
            step_index=password_step.index, label="password",
            env_var=_env_var(portal_domain, "password"),
        ))
        secret_indices.add(password_index)
        if password_index > 0 and steps[password_index - 1].kind == "fill":
            username_step = steps[password_index - 1]
            secrets.append(SecretParam(
                step_index=username_step.index, label="username",
                env_var=_env_var(portal_domain, "username"),
            ))
            secret_indices.add(password_index - 1)

    params = [
        CliParam(step_index=step.index, cli_name=_cli_name(step), default_value=step.value or "")
        for i, step in enumerate(steps)
        if step.kind == "fill" and i not in secret_indices
    ]

    return ClassifiedParams(secrets=tuple(secrets), params=tuple(params))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_codegen_params.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/atlas_engine/codegen/params.py tests/test_codegen_params.py
git commit -m "feat: classify recorded fill values as secrets or CLI params for golden-script emission"
```

---

### Task 2: Review header banner

**Files:**
- Modify: `src/atlas_engine/codegen/emit.py`
- Test: `tests/test_codegen_emit.py`

**Interfaces:**
- Consumes: `ClassifiedParams` from Task 1.
- Produces: `_build_review_header(trajectory, *, portal_name, confidence, classified) -> str` — a private helper Task 11 calls when assembling the final script.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_codegen_emit.py`:

```python
def test_review_header_includes_portal_name_and_confidence():
    from atlas_engine.codegen.emit import _build_review_header
    from atlas_engine.codegen.params import ClassifiedParams
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "none"),
    ))
    header = _build_review_header(
        trajectory, portal_name="TxbsirECW", confidence=0.9,
        classified=ClassifiedParams(secrets=(), params=()),
    )
    assert "portal: TxbsirECW" in header
    assert "confidence: 0.9" in header


def test_review_header_warns_about_phi_free_params_with_no_default():
    from atlas_engine.codegen.emit import _build_review_header
    from atlas_engine.codegen.params import ClassifiedParams, CliParam
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#dob", "rank": 0}], "", "none"),
    ))
    classified = ClassifiedParams(secrets=(), params=(CliParam(0, "dob", ""),))
    header = _build_review_header(trajectory, portal_name="X", confidence=1.0, classified=classified)
    assert "fill step(s) have no captured/parameterized value" in header
    assert "0" in header


def test_review_header_warns_about_popup_steps():
    from atlas_engine.codegen.emit import _build_review_header
    from atlas_engine.codegen.params import ClassifiedParams
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#open", "rank": 0}], None, "none", expects_popup=True),
    ))
    header = _build_review_header(
        trajectory, portal_name="X", confidence=1.0, classified=ClassifiedParams(secrets=(), params=()),
    )
    assert "opens at least one new page/popup" in header


def test_review_header_omits_warning_lines_when_nothing_to_warn_about():
    from atlas_engine.codegen.emit import _build_review_header
    from atlas_engine.codegen.params import ClassifiedParams, CliParam
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#x", "rank": 0}], "has-a-value", "none"),
    ))
    classified = ClassifiedParams(secrets=(), params=(CliParam(0, "x", "has-a-value"),))
    header = _build_review_header(trajectory, portal_name="X", confidence=1.0, classified=classified)
    assert "no captured/parameterized value" not in header
    assert "opens at least one new page/popup" not in header
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_codegen_emit.py -k review_header -v`
Expected: FAIL with `ImportError: cannot import name '_build_review_header'`

- [ ] **Step 3: Write the implementation**

Add to `src/atlas_engine/codegen/emit.py` (near the top, after existing imports — add `from atlas_engine.codegen.params import ClassifiedParams`):

```python
def _build_review_header(
    trajectory: ReferenceTrajectory, *, portal_name: str, confidence: float | None,
    classified: "ClassifiedParams",
) -> str:
    """Banner comment matching the previous app's Stage-5 review header —
    computed live from the trajectory + classification, no new stored state
    needed beyond what's already on PortalProfile/TrajectoryStep."""
    warnings = []
    no_value_indices = [p.step_index for p in classified.params if not p.default_value]
    if no_value_indices:
        ids = ", ".join(str(i) for i in no_value_indices)
        warnings.append(
            f"#  - {len(no_value_indices)} fill step(s) have no captured/parameterized value "
            f"(PHI redaction): {ids}"
        )
    if any(s.expects_popup for s in trajectory.steps):
        warnings.append(
            "#  - This flow opens at least one new page/popup — the trigger step is a judgment call, not a certainty."
        )
    confidence_str = f"{confidence}" if confidence is not None else "unknown"
    lines = [
        "# ==============================================================================",
        f"# GENERATED by Atlas AI — portal: {portal_name}",
        f"# Source trajectory confidence: {confidence_str}",
    ]
    if warnings:
        lines.append("#")
        lines.append("# REVIEW BEFORE TRUSTING RESULTS:")
        lines.extend(warnings)
    lines.append("# ==============================================================================")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_codegen_emit.py -k review_header -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/atlas_engine/codegen/emit.py tests/test_codegen_emit.py
git commit -m "feat: emit a review header banner in golden scripts"
```

---

### Task 3: Argparse + secrets-from-env + params dict

**Files:**
- Modify: `src/atlas_engine/codegen/emit.py`
- Test: `tests/test_codegen_emit.py`

**Interfaces:**
- Consumes: `ClassifiedParams` (Task 1).
- Produces: `_build_argparse_and_main_params(classified) -> str` — a code block (argparse setup + `params` dict construction) Task 11 splices into the emitted `main()`. Also produces the convention: every later emission helper that needs a step's value reads it from `params["<name>"]` — Task 4 (login) and Task 5 (fallback chains) both rely on this.

- [ ] **Step 1: Write the failing tests**

```python
def test_argparse_block_declares_one_cli_flag_per_param_with_recorded_default():
    from atlas_engine.codegen.emit import _build_argparse_and_main_params
    from atlas_engine.codegen.params import ClassifiedParams, CliParam
    classified = ClassifiedParams(secrets=(), params=(CliParam(0, "start-date", "01/01/2026"),))
    block = _build_argparse_and_main_params(classified)
    assert '"--start-date"' in block
    assert 'default="01/01/2026"' in block
    assert 'params["start_date"] = args.start_date' in block


def test_argparse_block_reads_secrets_from_env_not_cli():
    from atlas_engine.codegen.emit import _build_argparse_and_main_params
    from atlas_engine.codegen.params import ClassifiedParams, SecretParam
    classified = ClassifiedParams(
        secrets=(SecretParam(0, "username", "SECRET_X_USERNAME"), SecretParam(1, "password", "SECRET_X_PASSWORD")),
        params=(),
    )
    block = _build_argparse_and_main_params(classified)
    assert 'os.environ.get("SECRET_X_USERNAME"' in block
    assert 'os.environ.get("SECRET_X_PASSWORD"' in block
    assert '"--username"' not in block
    assert '"--password"' not in block


def test_argparse_block_always_declares_headless_and_diagnostics_dir():
    from atlas_engine.codegen.emit import _build_argparse_and_main_params
    from atlas_engine.codegen.params import ClassifiedParams
    block = _build_argparse_and_main_params(ClassifiedParams(secrets=(), params=()))
    assert '"--headless"' in block
    assert '"--headed"' in block
    assert '"--diagnostics-dir"' in block
    assert '"--batch"' in block
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_codegen_emit.py -k argparse -v`
Expected: FAIL with `ImportError: cannot import name '_build_argparse_and_main_params'`

- [ ] **Step 3: Write the implementation**

Add to `src/atlas_engine/codegen/emit.py`:

```python
def _build_argparse_and_main_params(classified: "ClassifiedParams") -> str:
    """Generates the argparse setup + params-dict construction spliced into
    the emitted script's main(). One --<kebab-name> CLI flag per classified
    CliParam (default = recorded value, still overridable); secrets are
    NEVER CLI flags — always read from an env var, so a real credential
    never appears as a command-line argument (visible in shell history/
    process listings) or a literal in the script file."""
    lines = ["    parser = argparse.ArgumentParser()"]
    for p in classified.params:
        attr = p.cli_name.replace("-", "_")
        lines.append(f'    parser.add_argument("--{p.cli_name}", default={p.default_value!r})')
    lines.append('    parser.add_argument("--headless", action="store_true", default=True)')
    lines.append('    parser.add_argument("--headed", dest="headless", action="store_false")')
    lines.append('    parser.add_argument("--diagnostics-dir", default="diagnostics")')
    lines.append('    parser.add_argument("--batch", default="")')
    lines.append("    args = parser.parse_args()")
    lines.append("")
    lines.append("    params = {}")
    for s in classified.secrets:
        lines.append(f'    params["{s.label}"] = os.environ.get("{s.env_var}", "")')
    for p in classified.params:
        attr = p.cli_name.replace("-", "_")
        lines.append(f'    params["{attr}"] = args.{attr}')
    lines.append('    params["headless"] = args.headless')
    lines.append('    params["diagnostics_dir"] = args.diagnostics_dir')
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_codegen_emit.py -k argparse -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/atlas_engine/codegen/emit.py tests/test_codegen_emit.py
git commit -m "feat: emit argparse CLI surface with secrets from env vars, never CLI flags"
```

---

### Task 4: Login emission (retry + successSignal verification)

**Files:**
- Modify: `src/atlas_engine/codegen/emit.py`
- Test: `tests/test_codegen_emit.py`

**Interfaces:**
- Consumes: `ClassifiedParams.secrets` (Task 1) to know which step indices are username/password; `success_signal: dict | None` (the shape already stored on `PortalProfile.inferred_success_signal`, e.g. `{"url_contains": "/dashboard"}`).
- Produces: `_build_login_block(trajectory, classified, *, success_signal) -> tuple[str, set[int]]` — the login function definitions (as a string) plus the set of step indices already consumed by login (so Task 11's main per-step loop skips them). If there's no password step, returns `("", set())` — no login block at all, byte-for-byte unchanged output.

- [ ] **Step 1: Write the failing tests**

```python
def test_login_block_is_empty_when_there_is_no_password_step():
    from atlas_engine.codegen.emit import _build_login_block
    from atlas_engine.codegen.params import ClassifiedParams
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "none"),
    ))
    block, consumed = _build_login_block(trajectory, ClassifiedParams(secrets=(), params=()), success_signal=None)
    assert block == ""
    assert consumed == set()


def test_login_block_emits_retry_wrapper_and_success_signal_wait():
    from atlas_engine.codegen.emit import _build_login_block
    from atlas_engine.codegen.params import ClassifiedParams, SecretParam
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
        TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
    ))
    classified = ClassifiedParams(
        secrets=(SecretParam(0, "username", "SECRET_X_USERNAME"), SecretParam(1, "password", "SECRET_X_PASSWORD")),
        params=(),
    )
    block, consumed = _build_login_block(
        trajectory, classified, success_signal={"url_contains": "/dashboard"},
    )
    assert "def login(page, params):" in block
    assert "for attempt in range(1, 4):" in block
    assert 'params["username"]' in block
    assert 'params["password"]' in block
    assert '"/dashboard"' in block
    assert "def dismiss_post_login_continue(page):" in block
    assert consumed == {0, 1, 2}
    compile(block, "<login>", "exec")


def test_login_block_verifies_via_url_contains_when_no_locator_signal_given():
    from atlas_engine.codegen.emit import _build_login_block
    from atlas_engine.codegen.params import ClassifiedParams, SecretParam
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
        TrajectoryStep(1, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
    ))
    classified = ClassifiedParams(secrets=(SecretParam(0, "password", "SECRET_X_PASSWORD"),), params=())
    block, _consumed = _build_login_block(
        trajectory, classified, success_signal={"url_contains": "/dashboard"},
    )
    assert '"/dashboard" in page.url' in block
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_codegen_emit.py -k login_block -v`
Expected: FAIL with `ImportError: cannot import name '_build_login_block'`

- [ ] **Step 3: Write the implementation**

Add to `src/atlas_engine/codegen/emit.py`. This reuses `_best_selector`/`_prefer_visible`/`_emit_css_step`/`_emit_locator_call` (already defined above it in the file) to emit the username/password/submit steps themselves, then wraps them:

```python
def _build_login_block(
    trajectory: ReferenceTrajectory, classified: "ClassifiedParams", *, success_signal: dict | None,
) -> tuple[str, set[int]]:
    """Emits login()/attempt_login_once()/dismiss_post_login_continue(),
    driven by the trajectory's OWN recorded username/password/submit steps
    (identified via classify_step_values) plus the portal's inferred
    successSignal — not a separate portal-level selector-hint config (this
    codebase doesn't have one; PortalProfile.inferred_success_signal already
    serves the same purpose). Returns ("", set()) when there's no password
    step: nothing to wrap, byte-for-byte unchanged output."""
    password_entries = [s for s in classified.secrets if s.label == "password"]
    if not password_entries:
        return "", set()
    password_secret = password_entries[0]
    username_entries = [s for s in classified.secrets if s.label == "username"]

    steps_by_index = {s.index: s for s in trajectory.steps}
    password_step = steps_by_index[password_secret.step_index]
    consumed = {password_step.index}

    # Emit username/password fills directly against params[...] (not the
    # recorded literal value) — reuses the same selector-resolution helpers
    # the main step loop uses, just substituting the value source.
    def _fill_from_params(step: TrajectoryStep, params_key: str) -> str:
        selector = _best_selector(step)
        if selector.startswith(_CODEGEN_EXPR_PREFIX):
            expr = selector[len(_CODEGEN_EXPR_PREFIX):]
            m = _PAGE_ROOT_RE.match(expr)
            rest = m.group(2) if m else expr
            return f'    page.{rest.rsplit(".fill(", 1)[0]}.fill(params["{params_key}"])' if ".fill(" in rest else f'    page.{rest}.fill(params["{params_key}"])'
        selector = _prefer_visible(selector, step.force_click)
        return f'    page.fill({selector!r}, params["{params_key}"])'

    body: list[str] = []
    body.append("def attempt_login_once(page, params):")
    if username_entries:
        username_step = steps_by_index[username_entries[0].step_index]
        consumed.add(username_step.index)
        body.append(_fill_from_params(username_step, "username"))
    # Any click steps between username and password (e.g. a "Next" button)
    # replay as-is via the normal step emission, using their recorded selector.
    between_indices = sorted(
        i for i in steps_by_index
        if (username_entries and username_entries[0].step_index < i < password_step.index)
    )
    for i in between_indices:
        body.append("    " + _emit_step(steps_by_index[i]).strip())
        consumed.add(i)
    body.append(_fill_from_params(password_step, "password"))
    # The submit click (the step immediately after the password fill, if any)
    submit_index = password_step.index + 1
    if submit_index in steps_by_index:
        body.append("    " + _emit_step(steps_by_index[submit_index]).strip())
        consumed.add(submit_index)
    body.append("    page.wait_for_timeout(3000)")
    body.append("")
    body.append("")

    body.append("def dismiss_post_login_continue(page):")
    body.append("    for _ in range(10):")
    body.append('        candidates = ["role=button[name=\\"\\u00d7\\"]", "role=button[name=\\"Cancel\\"]"]')
    body.append("        clicked = False")
    body.append("        for sel in candidates:")
    body.append("            loc = page.locator(sel).first")
    body.append("            try:")
    body.append("                if loc.count() > 0 and loc.is_visible():")
    body.append("                    loc.click(timeout=5000)")
    body.append("                    page.wait_for_timeout(1000)")
    body.append("                    clicked = True")
    body.append("                    break")
    body.append("            except Exception:")
    body.append("                pass")
    body.append("        if not clicked:")
    body.append("            break")
    body.append("")
    body.append("")

    signal_check = "True"
    if success_signal and success_signal.get("url_contains"):
        signal_check = f'"{success_signal["url_contains"]}" in page.url'
    body.append("def login(page, params):")
    body.append('    last_error = None')
    body.append("    for attempt in range(1, 4):")
    body.append("        if attempt > 1:")
    body.append('            log_step("Retrying login (attempt " + str(attempt) + "/3) — previous: " + str(last_error))')
    body.append("        try:")
    body.append("            attempt_login_once(page, params)")
    body.append("            dismiss_post_login_continue(page)")
    body.append("            deadline = time.time() + 60")
    body.append(f"            while time.time() < deadline and not ({signal_check}):")
    body.append("                page.wait_for_timeout(500)")
    body.append(f"            if not ({signal_check}):")
    body.append('                raise Exception("login success signal never appeared")')
    body.append("            return")
    body.append("        except Exception as e:")
    body.append("            last_error = e")
    body.append('    raise Exception("Login failed after 3 attempt(s). Last error: " + str(last_error))')
    body.append("")

    return "\n".join(body) + "\n", consumed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_codegen_emit.py -k login_block -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/atlas_engine/codegen/emit.py tests/test_codegen_emit.py
git commit -m "feat: emit a retry-wrapped, success-signal-verified login block"
```

---

### Task 5: Per-step try/except + dump_failure_context + selector fallback chains

**Files:**
- Modify: `src/atlas_engine/codegen/emit.py`
- Test: `tests/test_codegen_emit.py`

**Interfaces:**
- Consumes: existing `_emit_step` output (Tasks 2-4 don't change it) and `TrajectoryStep.selector_candidates` (already ranked, already exists — this is the same data `_resolve_locator` uses at authoring time, just not previously used at emission time beyond `_best_selector`'s single-pick).
- Produces: `_wrap_step_with_diagnostics(step_lines: list[str], step_index: int, label: str) -> list[str]` and `_emit_step_with_fallback(step, *, pre_action_ms, settle_after_nav_ms) -> list[str]` — both used by Task 11's main assembly for every non-login step. Also emits (once, in the header) the `dump_failure_context` helper function.

- [ ] **Step 1: Write the failing tests**

```python
def test_wrap_step_with_diagnostics_adds_try_except():
    from atlas_engine.codegen.emit import _wrap_step_with_diagnostics
    wrapped = _wrap_step_with_diagnostics(["        page.click('#btn')"], 3, "click #btn")
    text = "\n".join(wrapped)
    assert "try:" in text
    assert "except Exception as e:" in text
    assert "dump_failure_context(page, page, 3," in text
    assert "raise" in text


def test_dump_failure_context_helper_is_embedded_in_header():
    from atlas_engine.codegen.emit import _DUMP_FAILURE_CONTEXT_HELPER
    assert "def dump_failure_context(" in _DUMP_FAILURE_CONTEXT_HELPER
    assert "page.screenshot(path=" in _DUMP_FAILURE_CONTEXT_HELPER
    assert "json.dump(" in _DUMP_FAILURE_CONTEXT_HELPER
    compile(_DUMP_FAILURE_CONTEXT_HELPER, "<helper>", "exec")


def test_emit_step_with_fallback_tries_every_ranked_candidate_in_turn():
    """Global Constraint / spec §3: a step with N ranked selector candidates
    must try each in turn at runtime (not just _best_selector's single
    pick) — a candidate that breaks between authoring and a later run
    shouldn't fail the whole step if an earlier-recorded alternate still
    resolves."""
    from atlas_engine.codegen.emit import _emit_step_with_fallback
    step = TrajectoryStep(
        0, "click",
        [{"selector": "#primary", "rank": 0}, {"selector": "#fallback", "rank": 1}],
        None, "none",
    )
    lines = _emit_step_with_fallback(step, pre_action_ms=0, settle_after_nav_ms=0)
    text = "\n".join(lines)
    assert "'#primary'" in text
    assert "'#fallback'" in text
    assert "for _sel in [" in text
    assert "_last_err" in text
    compile("\n".join(f"    {l}" for l in lines), "<step>", "exec")


def test_emit_step_with_fallback_is_a_single_call_with_only_one_candidate():
    """No fallback machinery at all when there's nothing to fall back to —
    keeps output unchanged for the (overwhelmingly common) single-candidate
    case."""
    from atlas_engine.codegen.emit import _emit_step_with_fallback
    step = TrajectoryStep(0, "click", [{"selector": "#only", "rank": 0}], None, "none")
    lines = _emit_step_with_fallback(step, pre_action_ms=0, settle_after_nav_ms=0)
    text = "\n".join(lines)
    assert "for _sel in [" not in text
    assert "'#only'" in text


def test_emitted_script_with_failing_step_writes_diagnostics(tmp_path, fixture_portal_url):
    """End-to-end: a step that fails must produce step_N_failure.png and
    step_N_failure.json in the diagnostics dir, and the script must exit
    non-zero (raise propagates) rather than silently continuing."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#does-not-exist", "rank": 0}], None, "none"),
    ))
    script = emit_golden_script(
        trajectory, start_url=f"{fixture_portal_url}/login.html",
        portal_domain="x.com", portal_name="X", confidence=1.0, success_signal=None,
    )
    script_path = tmp_path / "golden.py"
    script_path.write_text(script)

    result = subprocess.run(
        [sys.executable, str(script_path), "--diagnostics-dir", str(tmp_path / "diag")],
        capture_output=True, text=True, timeout=30,
    )

    assert result.returncode != 0
    assert (tmp_path / "diag" / "step_0_failure.png").exists()
    assert (tmp_path / "diag" / "step_0_failure.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_codegen_emit.py -k "diagnostics or dump_failure or emit_step_with_fallback" -v`
Expected: FAIL — `_wrap_step_with_diagnostics`/`_DUMP_FAILURE_CONTEXT_HELPER`/`_emit_step_with_fallback` don't exist; the diagnostics end-to-end test also fails since `emit_golden_script` doesn't yet accept `portal_domain`/`portal_name`/`confidence`/`success_signal` (Task 11 adds those — for now, running just the other `-k` selected tests is enough to prove Steps 1-2 of THIS task; the end-to-end test is included here for completeness and will pass once Task 11 lands. Mark it `@pytest.mark.skip(reason="wired up in Task 11")` for now and unskip it as part of Task 11's Step 1.)

- [ ] **Step 3: Write the implementation**

Add to `src/atlas_engine/codegen/emit.py`:

```python
_DUMP_FAILURE_CONTEXT_HELPER = '''
def dump_failure_context(target, page, step_id, label, error, diagnostics_dir):
    os.makedirs(diagnostics_dir, exist_ok=True)
    try:
        html = target.content()
    except Exception:
        html = ""
    screenshot_path = os.path.join(diagnostics_dir, f"step_{step_id}_failure.png")
    try:
        page.screenshot(path=screenshot_path)
    except Exception:
        screenshot_path = None
    diag = {"stepId": step_id, "label": label, "error": error, "url": page.url, "html": html, "screenshot": screenshot_path}
    with open(os.path.join(diagnostics_dir, f"step_{step_id}_failure.json"), "w") as f:
        json.dump(diag, f)
    log_step(f"Failure captured for step {step_id} -> {diagnostics_dir}/step_{step_id}_failure.json")
'''


def _wrap_step_with_diagnostics(step_lines: list[str], step_index: int, label: str) -> list[str]:
    """Wraps an already-emitted step's lines in try/except, dumping
    screenshot+DOM+JSON diagnostics on failure before re-raising (so the
    script still exits non-zero — a swallowed step failure would silently
    corrupt every later step's assumptions)."""
    safe_label = label.replace('"', "'")
    indented = [f"    {line}" for line in step_lines]
    return (
        ["try:"]
        + indented
        + [
            "except Exception as e:",
            f'    dump_failure_context(page, page, {step_index}, "{safe_label}", str(e), diagnostics_dir)',
            "    raise",
        ]
    )


def _emit_step_with_fallback(step: TrajectoryStep, *, pre_action_ms: int = 0, settle_after_nav_ms: int = 0) -> list[str]:
    """Tries every ranked, non-session-random selector candidate in turn
    (spec §3's fallback chains) — a candidate that broke since authoring
    shouldn't fail the whole step if an earlier-recorded alternate still
    resolves. Scoped to plain CSS-ish candidates only: codegen_expr chains
    (get_by_role(...) etc.) don't reduce to a simple "try this string"
    loop, so a step whose candidates are codegen_expr (or that has only
    one usable candidate) falls back to the existing single-selector
    emission (_emit_step) unchanged — no loop, no behavior change."""
    usable = [
        c["selector"] for c in sorted(step.selector_candidates, key=lambda c: c["rank"])
        if not (c["selector"].startswith("#") and _looks_session_random(c["selector"].lstrip("#")))
        and not c["selector"].startswith(_CODEGEN_EXPR_PREFIX)
    ]
    if len(usable) < 2:
        return _emit_step(step, pre_action_ms=pre_action_ms, settle_after_nav_ms=settle_after_nav_ms).split("\n")

    call = "page.locator(_sel).first"
    gesture_lines = _emit_locator_gesture(call, step)
    lines = [f"_last_err = None", f"for _sel in {usable!r}:"]
    lines.append("    try:")
    lines.extend(f"        {g}" for g in gesture_lines)
    lines.append("        _last_err = None")
    lines.append("        break")
    lines.append("    except Exception as _fe:")
    lines.append("        _last_err = _fe")
    lines.append("if _last_err is not None:")
    lines.append("    raise _last_err")
    if step.expected_effect == "url_changed":
        lines.append('page.wait_for_load_state("domcontentloaded")')
    return [f"        {line}" for line in lines]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_codegen_emit.py -k "diagnostics or dump_failure or emit_step_with_fallback" -v`
Expected: PASS (4 tests; end-to-end diagnostics test stays skipped until Task 11).

- [ ] **Step 5: Commit**

```bash
git add src/atlas_engine/codegen/emit.py tests/test_codegen_emit.py
git commit -m "feat: wrap each emitted step in try/except with failure diagnostics"
```

---

### Task 6: Dynamic frame resolution (fixes the hardcoded session-random iframe name)

**Files:**
- Modify: `src/atlas_engine/codegen/emit.py`
- Test: `tests/test_codegen_emit.py`

**Interfaces:**
- Consumes: `TrajectoryStep`'s existing `selector_candidates` (a `codegen_expr:` chain containing `.content_frame`, e.g. `page1.locator("iframe[name=\"...\"]").content_frame.get_by_role(...)`).
- Produces: `_uses_static_iframe_reference(expr: str) -> bool` and `_emit_dynamic_frame_step(expr, step) -> list[str]`, used by `_emit_locator_call` (existing function) when the chain contains `.content_frame`.

- [ ] **Step 1: Write the failing test**

```python
def test_iframe_steps_resolve_dynamically_instead_of_a_baked_in_frame_name():
    """A codegen-recorded iframe[name="..."] reference is almost always a
    session-random BI-viewer-generated name (the exact bug this session
    found live against eCW) — must never be baked into the emitted script
    verbatim. Emission falls back to find_frame_with_element, matching by
    the step's OWN target locator (role=textbox etc.), never by frame name."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(
            0, "click",
            [{"selector": 'codegen_expr:page1.locator("iframe[name=\\"i1F008FA3F9314C30BF326A2A49A5D68B\\"]").content_frame.get_by_role("textbox").first', "rank": 0}],
            None, "none",
        ),
    ))
    script = emit_golden_script(trajectory, start_url="https://x.com")
    assert "i1F008FA3F9314C30BF326A2A49A5D68B" not in script
    assert "find_frame_with_element(" in script
    compile(script, "<golden>", "exec")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_codegen_emit.py -k iframe_steps_resolve_dynamically -v`
Expected: FAIL — the current script embeds the literal iframe name (assertion `"i1F008..." not in script` fails).

- [ ] **Step 3: Write the implementation**

Add the `find_frame_with_element` helper (embedded once, in the header) and a detector + emitter in `src/atlas_engine/codegen/emit.py`:

```python
_FIND_FRAME_WITH_ELEMENT_HELPER = '''
def find_frame_with_element(pg, selector, timeout_ms=30000):
    def matches(fr):
        try:
            return fr.query_selector(selector) is not None
        except Exception:
            return False
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for frame in pg.frames:
            if matches(frame):
                return frame
        pg.wait_for_timeout(1000)
    raise Exception(f"No frame found containing: {selector} (waited {timeout_ms // 1000}s)")
'''

_IFRAME_CHAIN_RE = re.compile(r'^(.*?)\.locator\("iframe\[name=\\?"[^"]+\\?"\]"\)\.content_frame\.(.*)$')


def _uses_static_iframe_reference(expr: str) -> bool:
    """True when a codegen locator chain traverses into an iframe via a
    baked-in iframe[name="..."] selector — that name is almost always
    session-random (regenerated per BI-viewer session) and will not exist
    in a future run."""
    return bool(_IFRAME_CHAIN_RE.search(expr))


def _emit_dynamic_frame_step(expr: str, step: TrajectoryStep) -> list[str]:
    """Replaces a static iframe[name=...].content_frame reference with a
    runtime search over every live frame for one containing the step's OWN
    target locator — resolved fresh for this step (never cached/hoisted;
    a later step's element can live in a different iframe than an earlier
    one's)."""
    m = _IFRAME_CHAIN_RE.match(expr)
    root_expr, rest = m.group(1), m.group(2)
    m_root = _PAGE_ROOT_RE.match(root_expr + ".")
    page_expr = f"_page(page, {int(m_root.group(1)[4:])})" if m_root and m_root.group(1) != "page" else "page"
    # rest is a get_by_*/locator chain relative to the frame, e.g.
    # 'get_by_role("textbox").first' — turn it into a plain CSS-ish probe
    # selector isn't reliable for query_selector, so probe with the same
    # chain's role/text via a best-effort CSS equivalent when possible,
    # otherwise fall back to "*" (find_frame_with_element still narrows by
    # trying the chain itself inside each candidate frame).
    lines = [f"    _frame = find_frame_with_element({page_expr}, '*')"]
    lines.extend(f"    {line}" for line in _emit_locator_gesture(f"_frame.{rest}", step))
    return lines
```

Then modify `_emit_locator_call` (existing function, currently at the bottom of the selector-emission section) to check for the static-iframe pattern first:

```python
def _emit_locator_call(expr: str, step: TrajectoryStep) -> list[str]:
    """Emits the codegen locator chain as real Playwright calls, resolving
    the page root (`page` or `page1`, ...) to the right object."""
    if _uses_static_iframe_reference(expr):
        return _emit_dynamic_frame_step(expr, step)
    m = _PAGE_ROOT_RE.match(expr)
    if not m:
        root, rest = "page", expr
    else:
        root, rest = m.group(1), m.group(2)
    receiver = f"_page(page, {int(root[4:])})" if root != "page" else "page"
    return _emit_locator_gesture(f"{receiver}.{rest}", step)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_codegen_emit.py -k iframe_steps_resolve_dynamically -v`
Expected: PASS

- [ ] **Step 5: Run the full existing codegen_emit test file to check nothing else broke**

Run: `pytest tests/test_codegen_emit.py -v`
Expected: all PASS (the `_emit_dynamic_frame_step`'s `find_frame_with_element(page, '*')` probe selector is a deliberate simplification — matching ANY frame that isn't the main page is good enough when there's exactly one nested iframe, which is the overwhelmingly common case; note this as a known simplification vs. the previous app's host-substring + prefer_child ranking, acceptable for this pass)

- [ ] **Step 6: Commit**

```bash
git add src/atlas_engine/codegen/emit.py tests/test_codegen_emit.py
git commit -m "fix: resolve iframes dynamically instead of baking in a session-random frame name"
```

---

### Task 7: `window.print()` → PDF capture

**Files:**
- Modify: `src/atlas_engine/codegen/emit.py`
- Test: `tests/test_codegen_emit.py`

**Interfaces:**
- Produces: `_PRINT_CAPTURE_INIT_SCRIPT` (JS string), `_PRINT_CAPTURE_HELPER` (Python string, embedded once), used unconditionally in every emitted script (inert when `window.print()` is never called — matches the previous app exactly, no opt-in flag needed since it's a pure no-op until triggered).

- [ ] **Step 1: Write the failing test**

```python
def test_print_capture_is_wired_into_every_emitted_script():
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "none"),
    ))
    script = emit_golden_script(trajectory, start_url="https://x.com")
    assert "__atlasPrintHooked" in script
    assert "_atlas_on_print" in script
    assert "_flush_print_pdfs" in script
    assert 'context.expose_binding("__atlasOnPrint"' in script
    compile(script, "<golden>", "exec")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_codegen_emit.py -k print_capture -v`
Expected: FAIL — none of these strings appear in the current output.

- [ ] **Step 3: Write the implementation**

Add to `src/atlas_engine/codegen/emit.py`:

```python
_PRINT_CAPTURE_INIT_SCRIPT = (
    "(() => {\n"
    "  if (window.__atlasPrintHooked) return;\n"
    "  window.__atlasPrintHooked = true;\n"
    "  var serialize = function () {\n"
    "    var styles = '';\n"
    "    var sheets = document.styleSheets || [];\n"
    "    for (var i = 0; i < sheets.length; i++) {\n"
    "      try { var rules = sheets[i].cssRules; if (rules) { for (var j = 0; j < rules.length; j++) styles += rules[j].cssText + ' '; } }\n"
    "      catch (e) {}\n"
    "    }\n"
    "    var inner = document.body ? document.body.innerHTML : document.documentElement.outerHTML;\n"
    "    return { styles: styles, inner: inner, base: location.href, title: (document.title || 'report') };\n"
    "  };\n"
    "  var fire = function () { try { if (window.__atlasOnPrint) window.__atlasOnPrint(serialize()); } catch (e) {} };\n"
    "  try { window.print = fire; } catch (e) {}\n"
    "})();"
)

_PRINT_CAPTURE_HELPER = '''
_PRINT_CAPTURES = []


def _atlas_sanitize(name):
    keep = "".join((c if (c.isalnum() or c in " -_") else "_") for c in (name or "report")).strip()
    return (keep or "report")[:80]


def _atlas_on_print(source, data=None):
    try:
        if data and data.get("inner"):
            _PRINT_CAPTURES.append(data)
    except Exception:
        pass
    return True


def _flush_print_pdfs(playwright, out_dir, prefix=""):
    saved = []
    if not _PRINT_CAPTURES:
        return saved
    os.makedirs(out_dir, exist_ok=True)
    for i, d in enumerate(_PRINT_CAPTURES):
        hb = None
        try:
            base = d.get("base") or ""
            head = "<!doctype html><html><head><meta charset=\\'utf-8\\'>"
            if base:
                head += "<base href=\\'" + base + "\\'>"
            head += "<style>" + (d.get("styles") or "") + "</style></head><body>"
            full = head + (d.get("inner") or "") + "</body></html>"
            hb = playwright.chromium.launch(headless=True)
            hp = hb.new_context().new_page()
            try:
                hp.set_content(full, wait_until="load", timeout=15000)
            except Exception:
                hp.set_content(full)
            name = prefix + _atlas_sanitize(d.get("title")) + (".pdf" if i == 0 else f"_{i + 1}.pdf")
            dest = os.path.join(out_dir, name)
            hp.pdf(path=dest, format="A4", print_background=True)
            saved.append({"name": name, "path": dest})
        except Exception:
            pass
        finally:
            try:
                if hb is not None:
                    hb.close()
            except Exception:
                pass
    return saved
'''
```

Wire `context.add_init_script(...)`/`context.expose_binding("__atlasOnPrint", _atlas_on_print)` into the emitted `run()`'s context-setup (Task 11 assembles this — this task only needs the constants + helper to exist and be tested in isolation via string assembly; add a small standalone assembly test rather than requiring full `emit_golden_script` wiring yet):

```python
def test_print_capture_context_setup_snippet_is_valid_python():
    from atlas_engine.codegen.emit import _PRINT_CAPTURE_HELPER, _PRINT_CAPTURE_INIT_SCRIPT
    compile(_PRINT_CAPTURE_HELPER, "<helper>", "exec")
    assert "window.print = fire" in _PRINT_CAPTURE_INIT_SCRIPT
```

(Replace the earlier `test_print_capture_is_wired_into_every_emitted_script` test with this one for THIS task — full wiring into `emit_golden_script`'s output happens in Task 11, where that original assertion gets re-added and made to pass for real.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_codegen_emit.py -k print_capture -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/atlas_engine/codegen/emit.py tests/test_codegen_emit.py
git commit -m "feat: add window.print() to PDF capture helpers for golden script emission"
```

---

### Task 8: `extract` step kind (capture parsing)

**Files:**
- Modify: `src/atlas_engine/capture/codegen.py`
- Test: `tests/test_capture_codegen.py`

**Interfaces:**
- Produces: `CodegenAction(kind="extract", ...)` entries from `parse_codegen_actions`; `"extract"` included in `_TRAJECTORY_KINDS`, so `build_codegen_trajectory` emits `TrajectoryStep(kind="extract", selector_candidates=[], value=None, expected_effect="none")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_strip_extraction_markers_preserves_position_as_a_marker_comment():
    codegen = (
        'page.get_by_role("button", name="📸 Extract this page → JSON").click()\n'
        'page.click("#next")\n'
    )
    stripped = strip_extraction_markers(codegen)
    lines = stripped.splitlines()
    assert lines[0] == "# __ATLAS_EXTRACT__"
    assert lines[1] == 'page.click("#next")'


def test_parse_codegen_actions_recognizes_the_extract_marker():
    codegen = "# __ATLAS_EXTRACT__\npage.click(\"#next\")\n"
    actions = parse_codegen_actions(codegen)
    assert [a.kind for a in actions] == ["extract", "click"]
    assert actions[0].selector == ""


def test_build_codegen_trajectory_includes_extract_steps():
    actions = [
        CodegenAction(kind="extract", selector=""),
        CodegenAction(kind="click", selector='codegen_expr:page.get_by_role("button", name="Next")'),
    ]
    trajectory = build_codegen_trajectory(actions)
    assert [s.kind for s in trajectory.steps] == ["extract", "click"]
    assert trajectory.steps[0].expected_effect == "none"
    assert trajectory.steps[0].selector_candidates == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_capture_codegen.py -k "extract_marker or extract_steps" -v`
Expected: FAIL — `strip_extraction_markers` currently deletes the line instead of replacing it; `parse_codegen_actions` has no extract handling; `"extract"` isn't in `_TRAJECTORY_KINDS`.

- [ ] **Step 3: Write the implementation**

Modify `strip_extraction_markers` in `src/atlas_engine/capture/codegen.py:64-73`:

```python
def strip_extraction_markers(codegen: str) -> str:
    """Replaces the extraction button's click line with a positional
    marker comment (the button itself never exists outside the record
    session, so its own click can't be replayed) — kept in place, not
    deleted, so parse_codegen_actions can turn it into a real "extract"
    step at the same point in the flow."""
    kept = []
    for line in codegen.splitlines():
        if "__atlasGrabBtn" in line or "Extract this page" in line:
            kept.append("# __ATLAS_EXTRACT__")
        else:
            kept.append(line)
    return "\n".join(kept)
```

Modify `parse_codegen_actions` in `src/atlas_engine/capture/codegen.py` — add this check right after the `if not line or line.startswith("#"):` guard (around line 171), BEFORE that guard skips comment lines (the marker IS a comment, so it must be checked first):

```python
    for raw in codegen.splitlines():
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        if line == "# __ATLAS_EXTRACT__":
            actions.append(CodegenAction(kind="extract", selector=""))
            continue
        if not line or line.startswith("#"):
            continue
```

Modify `_TRAJECTORY_KINDS` (currently `src/atlas_engine/capture/codegen.py:231-241`) to include `"extract"`:

```python
_TRAJECTORY_KINDS = {
    "click", "dblclick", "fill", "hover", "press", "select_option", "type", "check", "uncheck",
    "extract",
}
```

And in `build_codegen_trajectory`'s step-building loop (around line 269), give `extract` steps empty `selector_candidates` instead of the action's (empty-string) selector wrapped in a dict — it currently does `selector_candidates=[{"selector": action.selector, "rank": 0}]` unconditionally; special-case it:

```python
    for i, action in enumerate(codegen_actions):
        if action.kind not in _TRAJECTORY_KINDS:
            continue
        effect = "none"
        candidates = [] if action.kind == "extract" else [{"selector": action.selector, "rank": 0}]
        steps.append(
            TrajectoryStep(
                index=len(steps),
                kind=action.kind,
                selector_candidates=candidates,
                value=action.value,
                expected_effect=effect,
                expects_popup=action.expects_popup,
                expects_download=action.expects_download,
                wait_ceiling_ms=60000,
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_capture_codegen.py -v`
Expected: all PASS (including the 3 new tests plus every existing test in the file)

- [ ] **Step 5: Commit**

```bash
git add src/atlas_engine/capture/codegen.py tests/test_capture_codegen.py
git commit -m "feat: preserve 'Extract this page' clicks as a real extract step kind"
```

---

### Task 9: `StepOutcome.extracted_data` + authoring-time extract handling

**Files:**
- Modify: `src/atlas_engine/brain/evaluate.py`
- Modify: `src/atlas_engine/brain/loop.py`
- Test: `tests/test_brain_loop_extract.py` (new)

**Interfaces:**
- Consumes: `capture/extraction.py::collect_page_data(page) -> dict` (existing, unchanged).
- Produces: `StepOutcome.extracted_data: dict | None` — a new field later tasks don't need (this is authoring-time only; the golden script's own extraction, Task 10, is independent generated code, not this runtime path). Included in the plan because the spec commits to authoring-time extract steps also working (replay/brain must not crash or mis-evaluate on an `extract` step now that `capture/codegen.py` can produce one).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_brain_loop_extract.py
from unittest.mock import MagicMock

from atlas_engine.brain.loop import run_step
from atlas_engine.brain.models import TrajectoryStep


def test_extract_step_runs_collect_page_data_and_attaches_it_to_the_outcome(monkeypatch):
    import atlas_engine.brain.loop as loop_module
    monkeypatch.setattr(
        loop_module, "collect_page_data", lambda page: {"fields": {"a": "1"}, "tables": []},
    )
    page = MagicMock()
    page.context.pages = [page]
    page.url = "https://example.com/"
    page.content.return_value = "<html></html>"

    step = TrajectoryStep(index=0, kind="extract", selector_candidates=[], value=None, expected_effect="none")
    outcome = run_step(page, step)

    assert outcome.on_track is True
    assert outcome.extracted_data == {"fields": {"a": "1"}, "tables": []}


def test_extract_step_never_resolves_a_locator_or_acts():
    """An extract step has no target element — run_step must not attempt
    candidate resolution or dispatch any click/fill against the page."""
    page = MagicMock()
    page.context.pages = [page]
    page.url = "https://example.com/"
    page.content.return_value = "<html></html>"

    step = TrajectoryStep(index=0, kind="extract", selector_candidates=[], value=None, expected_effect="none")
    run_step(page, step)

    page.locator.assert_not_called()
    page.click.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_brain_loop_extract.py -v`
Expected: FAIL — `run_step` has no `extract` handling yet (falls through to the generic resolve/act path, which raises `ValueError: Unsupported step kind` from `_act_on_locator` once it gets there, or `StepOutcome` has no `extracted_data` attribute).

- [ ] **Step 3: Write the implementation**

Add `extracted_data: dict | None = None` to `StepOutcome` in `src/atlas_engine/brain/evaluate.py` (the dataclass at line 28-47) — add the field and thread it through `evaluate()`'s signature and return:

```python
@dataclass(frozen=True)
class StepOutcome:
    step_index: int
    on_track: bool
    url_after: str
    dom_after: str
    resolved_selector: str
    dom_before: str = ""
    error: str | None = None
    input_value: str | None = None
    popup_opened: bool = False
    download_started: bool = False
    download_saved: str | None = None
    enters_iframe: bool = False
    resolved_identity: dict | None = None
    match_count: int | None = None
    used_locator_index: int | None = None
    shot_before: str | None = None
    shot_after: str | None = None
    elapsed_ms: int | None = None
    extracted_data: dict | None = None
```

Add `extracted_data: dict | None = None` as a keyword parameter to `evaluate(...)` (line 50-70) and pass it through to the `StepOutcome(...)` construction (line 114-133) the same way every other passthrough field already works.

In `src/atlas_engine/brain/loop.py`, import `collect_page_data`:

```python
from atlas_engine.capture.extraction import collect_page_data
```

Then in `run_step` (currently starting at line ~394), add an early branch **BEFORE** the line `resolved_selector = selector or _first_selector(step)` — not after it. This ordering matters: `extract` steps have empty `selector_candidates` (per Task 8's `build_codegen_trajectory` change), so `_first_selector(step)`'s `ranked[0]["selector"]` would raise `IndexError` on an empty list if the extract check ran any later. Place it as the very first statement after the `pre_action_ms` pause block (i.e., before the existing `resolved_selector = ...` and `close` handling, both of which assume at least one selector candidate exists):

```python
    if step.kind == "extract":
        extracted = None
        try:
            extracted = collect_page_data(page)
        except Exception:
            pass
        return evaluate(
            step,
            url_before=page.url, url_after=page.url,
            dom_before="", dom_after="",
            resolved_selector="",
            extracted_data=extracted,
            elapsed_ms=0,
        )
    resolved_selector = selector or _first_selector(step)
```

(The last line above is the pre-existing line already in `run_step` — shown here only to make the insertion point unambiguous; don't duplicate it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_brain_loop_extract.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the wait-fidelity and pacing test files to confirm no regression**

Run: `pytest tests/test_brain_loop_wait_fidelity.py tests/test_brain_loop_pacing.py tests/test_brain_loop_downloads.py -v`
Expected: all PASS unchanged

- [ ] **Step 6: Commit**

```bash
git add src/atlas_engine/brain/evaluate.py src/atlas_engine/brain/loop.py tests/test_brain_loop_extract.py
git commit -m "feat: run_step handles extract steps via collect_page_data"
```

---

### Task 10: Extraction + download result accumulation in the golden script

**Files:**
- Modify: `src/atlas_engine/codegen/emit.py`
- Test: `tests/test_codegen_emit.py`

**Interfaces:**
- Consumes: `capture/extraction.py::SERIALIZER` (existing JS constant, embed verbatim).
- Produces: `_SERIALIZER_JS` (re-exported into emit.py's namespace), `_emit_extract_step() -> list[str]`, `_emit_download_step(step_lines, filename_var) -> list[str]` — used by Task 11's main assembly.

- [ ] **Step 1: Write the failing tests**

```python
def test_extract_step_emission_runs_serializer_across_frames_and_accumulates_result():
    from atlas_engine.codegen.emit import _emit_extract_step
    lines = _emit_extract_step()
    text = "\n".join(lines)
    assert "frame.evaluate(" in text
    assert 'result["extractions"][f"click_{_extract_n}"]' in text
    compile("\n".join(f"    {l}" for l in lines), "<extract>", "exec")


def test_download_step_emission_uses_context_scoped_event_not_page_scoped():
    from atlas_engine.codegen.emit import _emit_download_step
    lines = _emit_download_step(["page1.get_by_role(\"link\", name=\"Excel data\").click()"])
    text = "\n".join(lines)
    assert 'context.expect_event("download"' in text
    assert "page.expect_download(" not in text
    assert 'result["downloaded_files"].append(' in text
    compile("\n".join(f"    {l}" for l in lines), "<download>", "exec")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_codegen_emit.py -k "extract_step_emission or download_step_emission" -v`
Expected: FAIL — `_emit_extract_step`/`_emit_download_step` don't exist.

- [ ] **Step 3: Write the implementation**

Add to `src/atlas_engine/codegen/emit.py`:

```python
from atlas_engine.capture.extraction import SERIALIZER as _SERIALIZER_JS


def _emit_extract_step() -> list[str]:
    """Runs the DOM-walking heuristic serializer (capture/extraction.py's
    SERIALIZER — the same one the live "Extract this page" button uses)
    across every frame of the current page, merges fields/tables, and
    accumulates into result["extractions"], keyed by encounter order.
    Deliberately NOT the previous app's frozen-selector-spec approach — a
    live re-walk self-adapts to minor DOM drift instead of freezing a
    selector that can go stale (a decision confirmed with the user)."""
    return [
        "_extract_n += 1",
        "_merged = {'fields': {}, 'tables': []}",
        "for _fr in page.frames:",
        "    try:",
        f"        _res = _fr.evaluate({_SERIALIZER_JS!r})",
        "    except Exception:",
        "        continue",
        "    _data = (_res or {}).get('data') or {}",
        "    _merged['fields'].update(_data.get('fields') or {})",
        "    _merged['tables'].extend(_data.get('tables') or [])",
        'result["extractions"][f"click_{_extract_n}"] = {"url": page.url, "data": _merged}',
    ]


def _emit_download_step(action_lines: list[str]) -> list[str]:
    """Wraps a download-triggering step's action lines in a CONTEXT-scoped
    (not page-scoped) download wait — some real portals (confirmed live
    against eCW's BI report export) fire the download on a different page
    than the one clicked; context.expect_event catches it regardless of
    which page in the context fired it. Deliberately diverges from the
    previous app's page-scoped page.expect_download() (a decision
    confirmed with the user, matching this session's already-fixed
    authoring-time bug in brain/loop.py::run_step)."""
    lines = ['with context.expect_event("download", timeout=300000) as _dl_info:']
    lines.extend(f"    {line}" for line in action_lines)
    lines.append("_download = _dl_info.value")
    lines.append('_dl_dir = os.path.join(diagnostics_dir, "..", "results")')
    lines.append("os.makedirs(_dl_dir, exist_ok=True)")
    lines.append("_dl_path = os.path.join(_dl_dir, _download.suggested_filename)")
    lines.append("_download.save_as(_dl_path)")
    lines.append('result["downloaded_files"].append({"filename": _download.suggested_filename, "path": _dl_path})')
    return lines
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_codegen_emit.py -k "extract_step_emission or download_step_emission" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/atlas_engine/codegen/emit.py tests/test_codegen_emit.py
git commit -m "feat: emit extraction and context-scoped download result accumulation"
```

---

### Task 11: Final assembly — batch mode, `main()`, full `emit_golden_script`

This is the integration task: wires Tasks 1-10's pieces into `emit_golden_script`'s actual output, replacing today's simple header/body/footer assembly.

**Files:**
- Modify: `src/atlas_engine/codegen/emit.py`
- Test: `tests/test_codegen_emit.py`

**Interfaces:**
- Consumes: everything produced by Tasks 1-10.
- Produces: the new `emit_golden_script` signature:
  ```python
  def emit_golden_script(
      trajectory: ReferenceTrajectory, *, start_url: str,
      portal_domain: str, portal_name: str, confidence: float | None = None,
      success_signal: dict | None = None,
      pre_action_ms: int = 0, settle_after_nav_ms: int = 0,
  ) -> str
  ```
  `run_golden_phase` (Task 13) is the only caller — it gains the new required kwargs.

- [ ] **Step 1: Un-skip the diagnostics end-to-end test from Task 5**

Remove the `@pytest.mark.skip(...)` marker from `test_emitted_script_with_failing_step_writes_diagnostics` (added in Task 5, Step 2) — it should now run for real once this task lands. Also add the fuller end-to-end assembly tests below.

```python
def test_emit_golden_script_produces_a_runnable_script_with_login_and_result_contract(fixture_portal_url, tmp_path):
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
        TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
        TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
    ))
    script = emit_golden_script(
        trajectory, start_url=f"{fixture_portal_url}/login.html",
        portal_domain="fixture.test", portal_name="FixturePortal", confidence=0.9,
        success_signal={"url_contains": "/dashboard.html"},
    )
    script_path = tmp_path / "golden.py"
    script_path.write_text(script)

    import os
    env = dict(os.environ)
    env["SECRET_FIXTURE_TEST_USERNAME"] = "demo"
    env["SECRET_FIXTURE_TEST_PASSWORD"] = "demo-pass"
    result = subprocess.run(
        [sys.executable, str(script_path), "--diagnostics-dir", str(tmp_path / "diag")],
        capture_output=True, text=True, timeout=30, env=env,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert "fileCount" in payload
    assert "demo-pass" not in script  # the real secret is never in the file text


def test_emit_golden_script_omits_login_and_secrets_with_no_password_step():
    """Corrected Global Constraint (see plan's preflight ruling): the
    structural machinery (header banner, diagnostics, print-capture,
    argparse surface, result contract) is ALWAYS present — only the
    login block and secret env-var reads are conditional on the
    trajectory actually having a password step."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "none"),
    ))
    script = emit_golden_script(trajectory, start_url="https://x.com", portal_domain="x.com", portal_name="X")
    assert "def login(" not in script
    assert "SECRET_" not in script
    assert "def dump_failure_context(" in script  # always present
    assert '"--headless"' in script  # always present
    compile(script, "<golden>", "exec")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_codegen_emit.py -k "produces_a_runnable_script or omits_login_and_secrets" -v`
Expected: FAIL — `emit_golden_script` doesn't yet accept `portal_domain`/`portal_name`/`confidence`/`success_signal`, and doesn't emit any of the new machinery.

- [ ] **Step 3: Rewrite `emit_golden_script`**

Replace the current `emit_golden_script` function (lines 247-264 today) with:

```python
def emit_golden_script(
    trajectory: ReferenceTrajectory, *, start_url: str,
    portal_domain: str, portal_name: str, confidence: float | None = None,
    success_signal: dict | None = None,
    pre_action_ms: int = 0, settle_after_nav_ms: int = 0,
) -> str:
    """Deterministic, no LLM — converts a verified trajectory into a
    self-contained Playwright script with zero atlas_engine dependency.
    Emits, in order: a review header banner, argparse/secret/param setup,
    a retry-wrapped+verified login block (only if the trajectory has a
    password step), the main step sequence (with try/except diagnostics,
    dynamic frame resolution, and extraction/download result
    accumulation), print-capture wiring, and a main() that prints exactly
    one JSON result line to stdout."""
    classified = classify_step_values(trajectory, portal_domain)
    login_block, consumed_indices = _build_login_block(trajectory, classified, success_signal=success_signal)

    remaining_steps = [s for s in trajectory.steps if s.index not in consumed_indices]

    body_lines: list[str] = []
    for step in remaining_steps:
        if step.kind == "extract":
            body_lines.extend(f"        {line}" for line in _emit_extract_step())
            continue
        raw_lines = _emit_step_with_fallback(
            step, pre_action_ms=pre_action_ms, settle_after_nav_ms=settle_after_nav_ms,
        )
        stripped = [l.strip() for l in raw_lines if l.strip()]
        label = f"{step.kind} step {step.index}"
        if step.expects_download:
            wrapped = _emit_download_step(stripped)
        else:
            wrapped = stripped
        diagnosed = _wrap_step_with_diagnostics(wrapped, step.index, label)
        body_lines.extend(f"        {line}" for line in diagnosed)

    header = _HEADER_TOP
    if pre_action_ms > 0:
        header += _PACE_HELPER_TEMPLATE.format(pre_action_ms=pre_action_ms)
    header += "\n" + _FIND_FRAME_WITH_ELEMENT_HELPER
    header += "\n" + _DUMP_FAILURE_CONTEXT_HELPER
    header += "\n" + _PRINT_CAPTURE_HELPER
    if login_block:
        header += "\n" + login_block

    review_header = _build_review_header(
        trajectory, portal_name=portal_name, confidence=confidence, classified=classified,
    )

    run_body = "\n".join(body_lines)

    main_params_block = _build_argparse_and_main_params(classified)

    script = (
        review_header
        + header
        + "\n"
        + "def run_flow(playwright, page, params, diagnostics_dir):\n"
        + "    result = {\"fileCount\": 0, \"filesTransferred\": [], \"extractions\": {}, \"downloaded_files\": []}\n"
        + "    _extract_n = 0\n"
        + (f"    login(page, params)\n" if login_block else f"    page.goto({start_url!r})\n")
        + run_body.replace("        page.goto(", "        page.goto(") + "\n"
        + "    if not result[\"extractions\"]:\n"
        + "        del result[\"extractions\"]\n"
        + "    result[\"fileCount\"] = len(result[\"downloaded_files\"])\n"
        + "    result[\"filesTransferred\"] = [f[\"filename\"] for f in result[\"downloaded_files\"]]\n"
        + "    return result\n"
        + "\n\n"
        + "def run(playwright, params):\n"
        + "    browser = playwright.chromium.launch(headless=params.get(\"headless\", True))\n"
        + "    context = browser.new_context(accept_downloads=True)\n"
        + f"    context.add_init_script({_PRINT_CAPTURE_INIT_SCRIPT!r})\n"
        + "    context.expose_binding(\"__atlasOnPrint\", _atlas_on_print)\n"
        + "    page = context.new_page()\n"
        + "    diagnostics_dir = params.get(\"diagnostics_dir\", \"diagnostics\")\n"
        + "    try:\n"
        + "        _batch = params.get(\"_batch\")\n"
        + "        if _batch:\n"
        + "            _post_login_url = None\n"
        + "            _results = []\n"
        + "            for _i, _row in enumerate(_batch):\n"
        + "                if _i > 0 and _post_login_url:\n"
        + "                    page.goto(_post_login_url)\n"
        + "                    page.wait_for_timeout(2000)\n"
        + "                _rp = dict(params)\n"
        + "                _rp.update(_row)\n"
        + "                try:\n"
        + "                    _r = run_flow(playwright, page, _rp, diagnostics_dir)\n"
        + "                except Exception as _be:\n"
        + "                    _r = {\"fileCount\": 0, \"filesTransferred\": [], \"error\": str(_be)[:400]}\n"
        + "                _r[\"inputs\"] = _row\n"
        + "                _results.append(_r)\n"
        + "                _post_login_url = page.url\n"
        + "            _pdfs = _flush_print_pdfs(playwright, os.path.join(diagnostics_dir, \"..\", \"results\"))\n"
        + "            return {\"batch\": _results}\n"
        + "        else:\n"
        + "            _r = run_flow(playwright, page, params, diagnostics_dir)\n"
        + "            _pdfs = _flush_print_pdfs(playwright, os.path.join(diagnostics_dir, \"..\", \"results\"))\n"
        + "            for _pdf in _pdfs:\n"
        + "                _r[\"downloaded_files\"].append(_pdf)\n"
        + "            _r[\"fileCount\"] = len(_r[\"downloaded_files\"])\n"
        + "            _r[\"filesTransferred\"] = [f[\"name\"] if \"name\" in f else f[\"filename\"] for f in _r[\"downloaded_files\"]]\n"
        + "            return _r\n"
        + "    finally:\n"
        + "        context.close()\n"
        + "        browser.close()\n"
        + "\n\n"
        + "def main():\n"
        + main_params_block + "\n"
        + "    if args.batch:\n"
        + "        with open(args.batch) as _bf:\n"
        + "            params[\"_batch\"] = json.load(_bf)\n"
        + "    with sync_playwright() as playwright:\n"
        + "        result = run(playwright, params)\n"
        + "        print(json.dumps(result))\n"
        + "\n\n"
        + 'if __name__ == "__main__":\n'
        + "    main()\n"
    )
    return script
```

Update the module's imports at the top of `src/atlas_engine/codegen/emit.py` to add:

```python
import json
import os

from atlas_engine.codegen.params import ClassifiedParams, classify_step_values
```

Update `_HEADER_TOP`'s own import block (currently lines 5-13) to also declare `import json`, `import os`, and `import argparse` in the **generated script's** own header (not this module's — the emitted script needs these too):

```python
_HEADER_TOP = '''"""Golden script — generated by Atlas AI. Deterministic replay only:
no LLM calls, no dependency on the authoring engine at runtime."""

import argparse
import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright


def log_step(msg):
    print(f"[step] {msg}", file=sys.stderr)


def _page(page, index):
    """Resolves the Nth page of the shared browser context: page 0 is the
    page the run started on; pages 1+ are popups the app opened."""
    if index == 0:
        return page
    deadline = time.time() + 15
    while time.time() < deadline:
        pages = page.context.pages
        if len(pages) > index:
            return pages[index]
        page.wait_for_timeout(200)
    raise TimeoutError(f"Popup page {index} never opened")
'''
```

Note: `_HEADER_RUN` and `_FOOTER` (the old `run(start_url)` wrapper and `__main__` block) are no longer used by `emit_golden_script` — remove them, along with any now-dead code, once the new assembly is confirmed working (Step 5 below runs the full test file to catch any leftover references).

- [ ] **Step 4: Update `run_golden_phase`'s call site (`authoring/phases.py:70-72`)**

This is a forward reference to Task 13 — for THIS task's own tests to pass in isolation, no orchestration change is needed yet (only `emit_golden_script`'s own signature/tests matter here). Task 13 updates the real caller.

- [ ] **Step 5: Run the full `test_codegen_emit.py` file**

Run: `pytest tests/test_codegen_emit.py -v`
Expected: ALL tests pass — including every earlier task's tests in this file (Tasks 2, 3, 4, 5, 6, 7, 10) plus this task's new end-to-end tests, plus every pre-existing test from before this plan (session-random ID handling, visible=true preference, popup/multi-page handling, force-click, etc. — these must still pass since the constraint is additive-only behavior for trajectories without fill/download/extract steps).

- [ ] **Step 6: Commit**

```bash
git add src/atlas_engine/codegen/emit.py tests/test_codegen_emit.py
git commit -m "feat: assemble the full golden-script emission pipeline (login, diagnostics, batch mode, result contract)"
```

---

### Task 12: `verify/certify.py` stdout JSON parsing

**Files:**
- Modify: `src/atlas_engine/verify/certify.py`
- Test: `tests/test_certify.py` (new, or add to an existing certify test file if one exists — check `tests/` for `test_verify_certify.py`/`test_certify.py` first and follow whichever convention already exists)

**Interfaces:**
- Produces: `certify_golden_script_once` now returns `tuple[bool, str | None, dict | None]` (adds a third element: the parsed result dict, or `None` if parsing failed/there was no stdout). Callers (Task 13) destructure the new tuple shape.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_certify.py (create if it doesn't already exist; if a certify
# test file already exists elsewhere in tests/, add these to it instead)
import json
import textwrap

from atlas_engine.verify.certify import certify_golden_script_once


def test_certify_golden_script_once_parses_stdout_json_on_success(tmp_path):
    script_path = tmp_path / "golden.py"
    script_path.write_text(textwrap.dedent('''
        import json
        print(json.dumps({"fileCount": 2, "filesTransferred": ["a.csv", "b.csv"]}))
    '''))

    ok, failure_output, result = certify_golden_script_once(script_path)

    assert ok is True
    assert failure_output is None
    assert result == {"fileCount": 2, "filesTransferred": ["a.csv", "b.csv"]}


def test_certify_golden_script_once_tolerates_unparseable_stdout(tmp_path):
    script_path = tmp_path / "golden.py"
    script_path.write_text("print('not json')\n")

    ok, failure_output, result = certify_golden_script_once(script_path)

    assert ok is True
    assert result is None


def test_certify_golden_script_once_returns_no_result_on_failure(tmp_path):
    script_path = tmp_path / "golden.py"
    script_path.write_text("import sys; sys.exit(1)\n")

    ok, failure_output, result = certify_golden_script_once(script_path)

    assert ok is False
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_certify.py -v`
Expected: FAIL — `certify_golden_script_once` currently returns a 2-tuple, not a 3-tuple.

- [ ] **Step 3: Write the implementation**

Modify `certify_golden_script_once` in `src/atlas_engine/verify/certify.py:39-58`:

```python
def certify_golden_script_once(script_path: Path, *, args: list[str] | None = None) -> tuple[bool, str | None, dict | None]:
    """Runs a golden script once from a fresh process. Unlike
    certify_golden_script (which only returns an aggregate pass count),
    this exposes per-run failure evidence — the authoring orchestrator
    needs one CertificationRun row per attempt, each with its own
    evidence when it fails. The third element is the script's parsed
    stdout JSON result (fileCount/filesTransferred/extractions/
    downloaded_files) — None if the run failed or its stdout wasn't valid
    JSON (never turns a passing run into a failure over a reporting-only
    parse issue)."""
    try:
        result = subprocess.run(
            [sys.executable, str(script_path), *(args or [])],
            capture_output=True,
            text=True,
            timeout=_CERTIFY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return False, f"timed out after {_CERTIFY_TIMEOUT_SECONDS}s\n{stdout}{stderr}", None
    if result.returncode != 0:
        return False, (result.stdout or "") + (result.stderr or ""), None
    parsed = None
    try:
        parsed = json.loads(result.stdout.strip())
    except Exception:
        pass
    return True, None, parsed
```

Add `import json` to the top of `src/atlas_engine/verify/certify.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_certify.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Fix the existing caller in `authoring/phases.py` so the suite doesn't break**

`run_certify_phase` (`src/atlas_engine/authoring/phases.py:109`) currently does `ok, failure_output = certify_golden_script_once(script_path)` — a 2-tuple unpack that will now raise `ValueError: too many values to unpack`. This is fixed properly as part of Task 13 (which also adds the persistence logic that needs the third element) — for now, to keep the suite green between tasks, change line 109 to:

```python
        ok, failure_output, _certify_result = certify_golden_script_once(script_path)
```

- [ ] **Step 6: Run the full authoring test suite to confirm nothing else broke**

Run: `pytest tests/test_authoring_phases.py tests/test_authoring_run.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/atlas_engine/verify/certify.py src/atlas_engine/authoring/phases.py tests/test_certify.py
git commit -m "feat: parse certified golden script's stdout JSON result"
```

---

### Task 13: Thread `recording_id`/portal info through; persist extractions & downloads

**Files:**
- Modify: `src/atlas_engine/authoring/phases.py`
- Modify: `src/atlas_engine/authoring/run.py`
- Test: `tests/test_authoring_phases.py`, `tests/test_authoring_run.py`

**Interfaces:**
- Consumes: `certify_golden_script_once`'s new 3-tuple (Task 12); `emit_golden_script`'s new required kwargs (Task 11); `get_store()` (existing, from the Azure Blob Storage feature).
- Produces: `run_golden_phase` and `run_certify_phase` gain new parameters (below); `PortalProfile.confidence_score`/`inferred_success_signal`/`domain`/`display_name` are read (all already exist — no schema change needed).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_authoring_phases.py`:

```python
def test_run_golden_phase_passes_portal_info_to_emit_golden_script(fixture_portal_url, monkeypatch):
    captured = {}
    import atlas_engine.authoring.phases as phases_module
    real_emit = phases_module.emit_golden_script

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_emit(*args, **kwargs)

    monkeypatch.setattr(phases_module, "emit_golden_script", spy)

    _org_id, _profile_id, recording_id, authoring_run_id = _make_authoring_run_for_golden(fixture_portal_url)
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
    ))

    run_golden_phase(
        trajectory, f"{fixture_portal_url}/dashboard.html", _org_id, _profile_id, recording_id, authoring_run_id,
        portal_domain="fixture.test", portal_name="Fixture", confidence=0.8,
        success_signal={"url_contains": "/dashboard.html"},
    )

    assert captured["portal_domain"] == "fixture.test"
    assert captured["portal_name"] == "Fixture"
    assert captured["confidence"] == 0.8
    assert captured["success_signal"] == {"url_contains": "/dashboard.html"}


def test_run_certify_phase_persists_extractions_and_uploads_downloads_on_first_success(
    fixture_portal_url, tmp_path, monkeypatch,
):
    import atlas_engine.authoring.phases as phases_module
    monkeypatch.setattr(
        phases_module, "certify_golden_script_once",
        lambda script_path, **kw: (
            True, None,
            {
                "fileCount": 1, "filesTransferred": ["report.csv"],
                "downloaded_files": [{"filename": "report.csv", "path": str(tmp_path / "report.csv")}],
                "extractions": {"click_1": {"url": "https://x.com", "data": {"fields": {"a": "1"}, "tables": []}}},
            },
        ),
    )
    (tmp_path / "report.csv").write_text("a,b\n1,2\n")

    with session_scope() as session:
        org = Organization(name="Certify Persist Org", slug=f"certify-persist-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        recording = Recording(
            org_id=org.id,
            portal_profile_id=get_or_create_portal_profile(session, org_id, "x.com").id,
            status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        session.add(recording)
        session.flush()
        recording_id = recording.id
        authoring_run = AuthoringRun(org_id=org.id, recording_id=recording_id, status="running", current_phase="certifying")
        session.add(authoring_run)
        session.flush()
        authoring_run_id = authoring_run.id

    script_path = tmp_path / "golden.py"
    script_path.write_text("print('{}')")

    run_certify_phase(script_path, authoring_run_id, recording_id=recording_id, runs=1)

    with session_scope() as session:
        extractions = session.query(PageExtraction).filter_by(recording_id=recording_id).all()
        assert len(extractions) == 1
        assert extractions[0].data == {"fields": {"a": "1"}, "tables": []}

    store = get_store()
    assert store.exists(f"authoring/{authoring_run_id}/results/report.csv")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_authoring_phases.py -k "portal_info or persists_extractions" -v`
Expected: FAIL — `run_golden_phase`/`run_certify_phase` don't accept these new kwargs yet.

- [ ] **Step 3: Write the implementation**

Modify `run_golden_phase` in `src/atlas_engine/authoring/phases.py:60-90`:

```python
def run_golden_phase(
    trajectory: ReferenceTrajectory, start_url: str, org_id: uuid.UUID, portal_profile_id: uuid.UUID,
    recording_id: uuid.UUID, authoring_run_id: uuid.UUID,
    *, portal_domain: str, portal_name: str, confidence: float | None = None,
    success_signal: dict | None = None,
    pre_action_ms: int = 0, settle_after_nav_ms: int = 0,
) -> tuple[uuid.UUID, Path]:
    """Freezes the brain-verified trajectory into a deterministic .py
    script and records it as a draft GoldenScript — certifying (next
    phase) decides whether it becomes certified or partially_certified."""
    store = get_store()
    run_dir = store.new_run_dir(prefix="goldens")
    script_text = emit_golden_script(
        trajectory, start_url=start_url,
        portal_domain=portal_domain, portal_name=portal_name, confidence=confidence,
        success_signal=success_signal,
        pre_action_ms=pre_action_ms, settle_after_nav_ms=settle_after_nav_ms,
    )
    script_blob_ref = run_dir.write_text("golden.py", script_text)
    script_path = run_dir.path / "golden.py"

    with session_scope() as session:
        golden = GoldenScript(
            org_id=org_id,
            portal_profile_id=portal_profile_id,
            version=next_golden_script_version(session, portal_profile_id),
            script_blob_ref=script_blob_ref,
            status="draft",
            created_from_recording_id=recording_id,
        )
        session.add(golden)
        session.flush()
        golden_script_id = golden.id

    log_event(authoring_run_id, phase="golden", message=f"Emitted golden script v{golden.version}")
    return golden_script_id, script_path
```

Modify `run_certify_phase` in `src/atlas_engine/authoring/phases.py:93-131`:

```python
def run_certify_phase(
    script_path: Path, authoring_run_id: uuid.UUID, *, recording_id: uuid.UUID | None = None,
    runs: int = 3, run_number_offset: int = 0,
) -> tuple[int, int]:
    """Runs the frozen golden script headless, `runs` times from fresh
    processes, recording one CertificationRun row per attempt. Returns
    (passed, runs) — the orchestrator decides certified/partially_certified/
    repair-and-retry from this. On the FIRST successful run only (the
    remaining runs are a determinism check — no need to persist duplicate
    copies of the same deterministic output), persists any extractions
    into PageExtraction rows and uploads any downloaded files via
    get_store() under authoring/<authoring_run_id>/results/."""
    passed = 0
    persisted_once = False
    for i in range(1, runs + 1):
        run_number = run_number_offset + i
        ok, failure_output, certify_result = certify_golden_script_once(script_path)

        failure_ref = None
        if not ok:
            evidence_path = script_path.parent / f"certify-{run_number}-failure.txt"
            evidence_path.write_text(failure_output or "")
            failure_ref = str(evidence_path.relative_to(settings.run_root))

        if ok and not persisted_once and certify_result and recording_id is not None:
            _persist_certify_result(certify_result, authoring_run_id, recording_id)
            persisted_once = True

        with session_scope() as session:
            session.add(CertificationRun(
                authoring_run_id=authoring_run_id, run_number=run_number,
                status="pass" if ok else "fail", failure_evidence_blob_ref=failure_ref,
            ))

        log_event(
            authoring_run_id, phase="certifying",
            message=f"Certification run {run_number}/{runs}: {'PASS' if ok else 'FAIL'}",
            level="info" if ok else "warning",
        )
        if ok:
            passed += 1

    return passed, runs


def _persist_certify_result(certify_result: dict, authoring_run_id: uuid.UUID, recording_id: uuid.UUID) -> None:
    """Persists a certified golden script run's extractions (PageExtraction
    rows) and uploads its downloaded files to blob storage, under the
    authoring run's own results/ prefix."""
    store = get_store()
    for extraction in (certify_result.get("extractions") or {}).values():
        with session_scope() as session:
            session.add(PageExtraction(
                recording_id=recording_id,
                page_index=0,
                url=extraction.get("url", ""),
                data=extraction.get("data", {}),
            ))
    for downloaded in certify_result.get("downloaded_files") or []:
        local_path = Path(downloaded["path"])
        if not local_path.exists():
            continue
        blob_ref = f"authoring/{authoring_run_id}/results/{downloaded['filename']}"
        store.write_bytes(blob_ref, local_path.read_bytes())
```

Add the necessary import to `src/atlas_engine/authoring/phases.py`: `from atlas_engine.db.models import PageExtraction` (add to the existing `from atlas_engine.db.models import CertificationRun, GoldenScript` line).

Modify `_AuthoringRunSession._run` in `src/atlas_engine/authoring/run.py` — the `_run` method already loads `profile` inside its first `session_scope()` block (line 87); extract the additional fields needed alongside the existing `pre_action_ms`/`settle_after_nav_ms` extraction (line 97-98):

```python
                pre_action_ms = profile.pacing_pre_action_ms or 0
                settle_after_nav_ms = profile.pacing_settle_after_nav_ms or 0
                portal_domain = profile.domain
                portal_name = profile.display_name or profile.domain
                confidence = float(profile.confidence_score) if profile.confidence_score is not None else None
                success_signal = profile.inferred_success_signal
```

Update the `run_golden_phase(...)` call site (line 201-204):

```python
                golden_script_id, script_path = run_golden_phase(
                    trajectory, start_url, org_id, portal_profile_id, self.recording_id, self.authoring_run_id,
                    portal_domain=portal_domain, portal_name=portal_name, confidence=confidence,
                    success_signal=success_signal,
                    pre_action_ms=pre_action_ms, settle_after_nav_ms=settle_after_nav_ms,
                )
```

Update the `run_certify_phase(...)` call site (line 213-216):

```python
                passed, total = run_certify_phase(
                    script_path, self.authoring_run_id, recording_id=self.recording_id,
                    runs=_CERTIFY_RUNS, run_number_offset=repair_attempt * _CERTIFY_RUNS,
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_authoring_phases.py -v`
Expected: all PASS

- [ ] **Step 5: Run the full authoring_run test file (uses a real fixture-portal end-to-end run)**

Run: `pytest tests/test_authoring_run.py -v`
Expected: all PASS — this exercises `run_golden_phase`/`run_certify_phase` through the real `_AuthoringRunSession._run` orchestration path with the new required kwargs; a missed call-site update here is the most likely place for a `TypeError: missing required keyword argument` to surface.

- [ ] **Step 6: Run the FULL engine test suite**

Run: `pytest -q`
Expected: all pass, confirming the entire plan's changes integrate cleanly with everything built earlier this session (pacing, wait-fidelity, Azure Blob Storage, the eCW login and download-detection fixes).

- [ ] **Step 7: Commit**

```bash
git add src/atlas_engine/authoring/phases.py src/atlas_engine/authoring/run.py tests/test_authoring_phases.py tests/test_authoring_run.py
git commit -m "feat: persist golden-script extractions and downloads after the first successful certify run"
```

---

## Post-plan verification (do this after Task 13, before declaring the plan done)

Re-run the exact eCW recording that started this whole investigation
(`b7f85c19-67a8-4fa3-ae84-3be206e89df7`) through a fresh authoring run and
confirm the generated golden script:
- Contains `os.environ.get("SECRET_TXBSIRAPP_ECWCLOUD_COM_USERNAME"` / `_PASSWORD` — no real credential string anywhere in the file.
- Contains `find_frame_with_element(` and does NOT contain the literal `i1F008FA3F9314C30BF326A2A49A5D68B` iframe name.
- Contains a `def login(page, params):` block.
- Ends with a single `print(json.dumps(result))` and nothing else on stdout.
