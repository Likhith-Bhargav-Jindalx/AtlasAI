# Recording Library Completion — Design Spec

## Goal

Close six remaining gaps in the AtlasAI console, identified by auditing all 16 prior plan docs against the current code:

1. Operations-agent recording library (video + steps, no code)
2. Developer request-queue / claim screen
3. Editable recording title/description + "submit to developer"
4. Server-side role enforcement
5. Heal-approval before/after screenshots
6. LLM cost/token dashboard

Out of scope (explicitly, per user instruction): desktop packaging, cloud multi-tenant backend, voice narration capture.

**Verified already done, not part of this spec:** developers already see the codegen Playwright script alongside the video on `AuthoringPage` (reached from `LibraryPage`). Replaying codegen action kinds beyond click/fill is also already fully implemented (`_TRAJECTORY_KINDS`, `_act_on_locator`, and `evaluate.py` already cover click/dblclick/hover/hover_click/dispatch_click/type/fill/press/check/uncheck/select_option/goto, matching `/Users/apple/Desktop/atlasAI/core/src/brain/brainLoop.ts`'s gesture vocabulary exactly).

## Hard constraint: the self-healing core does not change

The engine's brain/replay/repair/evaluate logic was iteratively ported from `/Users/apple/Desktop/atlasAI/` across five ledgered rounds and is verified against real portal recordings. **No task in this spec may modify:**

- `engine/src/atlas_engine/brain/loop.py`
- `engine/src/atlas_engine/brain/repair.py`
- `engine/src/atlas_engine/brain/evaluate.py`
- `engine/src/atlas_engine/brain/blockers.py`
- `engine/src/atlas_engine/capture/codegen.py` (parsing/trajectory-building logic)
- `engine/src/atlas_engine/authoring/replay.py`
- `engine/src/atlas_engine/codegen/emit.py`

The one call site that touches this boundary (Task 5, passing `shot_dir` into an existing `BrainLoop.run()` call) is additive only — `shot_dir` is an existing optional parameter already used elsewhere (the authoring brain phase), so this changes zero decision logic, only turns on a side effect the loop already supports.

**Regression gate:** every task below ends with the full existing engine suite (`cd engine && .venv/bin/pytest -q`, currently 197+ passing) and the full frontend suite (`cd frontend && npm test && npm run lint && npm run build`) staying green. A task that touches a shared file re-runs the full suite, not just its own new tests.

## Global conventions (follow existing patterns)

- Service functions return `list[dict]` / `dict`, live in `*_service.py` or a domain module (`verify/heal.py`, `recording_service.py`); endpoints in `api.py` are thin wrappers with Pydantic request/response models.
- New tables use Alembic migrations under `engine/migrations/versions/` (see existing files for the pattern).
- New frontend data hooks follow `frontend/src/lib/{recordings,authoring,healEvents}.ts`'s React Query pattern.
- Existing role fields: `User.role` is the org tier (`owner`/`admin`/`member`); `User.job_function` (`operations_agent`/`developer`) is what actually distinguishes an agent from a developer in this app and is what the console's role switcher already keys off (`frontend/src/layout/RoleContext.tsx`).

---

## Task 1: Operations-agent recording library

**Problem:** `InboxPage` (`/agent`) lists open *requests* only. There is no browsable history of an agent's own past recordings — `ReviewPage` (`/agent/recordings/:id`, video + steps, no code) already exists but is only reachable right after finishing a recording.

**Backend:** `list_recordings(org_id, status=None, user_id=None)` in `recording_service.py` gains an optional `user_id` filter (`query.filter_by(user_id=uuid.UUID(user_id))` when given). `GET /recordings` endpoint gains an optional `user_id` query param passed through.

**Frontend:** New `frontend/src/pages/agent/AgentLibraryPage.tsx` at route `/agent/library`. Reuses `useRecordings` (extended to accept `userId`) and the same card layout as `LibraryPage`, minus the codegen-script badge (agents never see code). Cards navigate to `/agent/recordings/:id` (existing `ReviewPage`, unchanged). Add `{ to: '/agent/library', label: 'Recording library' }` to `NAV.agent` in `AppLayout.tsx`.

**Tests:** `test_recording_service.py` — `user_id` filter returns only that user's recordings. `AgentLibraryPage.test.tsx` — renders cards without a codegen badge, navigates to `ReviewPage` on click.

---

## Task 2: Developer request-queue / claim screen

**Problem:** `list_recording_requests` and `POST /recording-requests/{id}/claim` already exist and work; there is no frontend page for a developer to see open requests and claim one.

**Backend:** None needed.

**Frontend:** New `frontend/src/pages/dev/RequestQueuePage.tsx` at route `/dev/requests`. Lists requests with status `open` or `submitted` via `useRecordingRequests(orgId)` (already exists, used by `InboxPage`). Each row has a "Claim" button calling a new `useClaimRecordingRequest()` hook wrapping `POST /recording-requests/{id}/claim` with the current developer's `user_id` (from `useAuth()`). A second section lists requests already claimed by the current user (`claimed_by === user.user_id`), each linking to the matching recording's `/dev/recordings/:id` once one exists (match via `recording.request_id === request.id`; until then, show "Waiting for a recording"). Add `{ to: '/dev/requests', label: 'Request queue' }` to `NAV.dev`.

**Tests:** `RequestQueuePage.test.tsx` — claim button fires the mutation and disables while pending; claimed section shows the link once a matching recording exists in the mocked fetch.

---

## Task 3: Editable recording title/description + "submit to developer"

**Problem:** Only `RecordingRequest` has `title`/`description`. An agent who records ad-hoc (`role="demo"`, no `request_id`, via InboxPage's "Start new recording" button) has no way to name it or hand it to a developer.

**Data model:** Migration adds `title: str | None` and `description: str | None` to `Recording`.

**Backend:**
- `update_recording_details(recording_id, *, title=None, description=None) -> None` in `recording_service.py`; `PATCH /recordings/{id}` endpoint (body: `{title?, description?}`).
- `submit_recording_for_authoring(recording_id: uuid.UUID, submitted_by: uuid.UUID) -> uuid.UUID` in `recording_service.py`: loads the `Recording` and its `PortalProfile`, raises `ValueError` if `recording.request_id is not None` (already linked), creates a `RecordingRequest(org_id=recording.org_id, requested_by=submitted_by, portal_profile_id=recording.portal_profile_id, target_url=profile.inferred_login_url or f"https://{profile.domain}", title=recording.title or "Untitled recording", description=recording.description, status="submitted")`, sets `recording.request_id` to the new request's id, returns the request id. `POST /recordings/{id}/submit` endpoint (body: `{submitted_by: str}`).
- `get_recording_detail` gains `title`/`description` in its response dict; `RecordingDetailResponse` gains the two optional fields.

**Frontend:** On `ReviewPage` (agent view), when `recording.request_id` is null: editable title/description inputs (PATCH on blur, debounced or on-submit) and a "Submit to developer" button calling the new submit endpoint; on success, navigate back to `/agent` with a confirmation, since the recording now has a request and shows up in the developer's queue (Task 2).

**Tests:** `test_recording_service.py` — `update_recording_details` persists fields; `submit_recording_for_authoring` creates a linked request and rejects an already-linked recording. `test_api_recordings.py` — both endpoints. `ReviewPage.test.tsx` — submit button hidden once `request_id` is set, shown otherwise.

---

## Task 4: Server-side role enforcement

**Problem:** Every endpoint trusts the caller-supplied `org_id`/`user_id` for identity with no permission check — any client can call `/heal-events/{id}/approve` or `/recording-requests/{id}/claim` regardless of who they are.

**Backend (`api.py`):**
- `get_current_user(atlas_session: str | None = Cookie(default=None)) -> CurrentUser` dependency: verifies the session cookie exactly as `/auth/me` does today, loads the `User` row, returns a small `CurrentUser(id, org_id, role, job_function)` value object (not the ORM row, to avoid detached-instance issues). 401 on missing/invalid/expired session or a deleted user — same failure modes `/auth/me` already handles.
- `require_job_function(*allowed: str)` dependency factory: 403s unless `user.job_function in allowed` **or** `user.role in ("owner", "admin")` (org admins bypass job-function gating). Depends on `get_current_user`.
- Applied via `Depends(...)` on: `claim_recording_request_endpoint`, `author_from_recording_endpoint`, the authoring-run resume/stop endpoints, `approve_endpoint`, `reject_endpoint`, and the new `submit` endpoint from Task 3 → `require_job_function("developer")` for the dev-side ones, `require_job_function("operations_agent")` for submit. All `GET`/list endpoints stay open to any authenticated org member (`Depends(get_current_user)` only, checking `user.org_id == <requested org_id>` and 403 on mismatch — prevents cross-org data access, which today is also unchecked).

**Frontend:** No changes required — the frontend already calls these endpoints only from the correct role's pages, and the client already carries the session cookie (`credentials: 'include'` per the existing CORS setup). A 403 surfaces through the existing error-handling paths (`isError` states already rendered on every mutation in these pages).

**Tests:** New `test_api_auth_guards.py` — each guarded endpoint returns 403 for the wrong `job_function` and 401 with no session; GET endpoints return 403 for a mismatched `org_id`. Full existing suite re-run since this wraps shared endpoints.

---

## Task 5: Heal-approval before/after screenshots

**Problem:** `ApprovalsPage` shows only old/new selector text. `run_step` in `brain/loop.py` already captures per-step before/after screenshots when given a `shot_dir` (used by the authoring brain phase) — `heal_golden_script` in `verify/heal.py` just never passes one, so no images exist for heal events today.

**Backend (`verify/heal.py` only — `BrainLoop.run()` itself is untouched):**
- `heal_golden_script` creates a run-scoped shot directory via the existing `LocalRunStore` (`store.new_run_dir(prefix="heal-shots")`) before the `BrainLoop(...).run(old_trajectory)` call, and passes it as the existing `shot_dir` argument.
- `compute_trajectory_diff(old, new)` gains an optional `shots_by_index: dict[int, tuple[str|None, str|None]]` parameter (built from `result`'s per-step outcomes' `ref_shot_before`/`ref_shot_after`-equivalent fields, whatever `run_step` already populates when `shot_dir` is set); each diff entry gets `before_screenshot`/`after_screenshot` relative paths when available (`None` if a step had no screenshot, e.g. it was already on-track).
- New `GET /heal-events/{heal_event_id}/screenshots/{step_index}/{when}` endpoint (`when` is `before`/`after`), same `FileResponse` pattern as `get_recording_video_endpoint`, serving the path recorded in that diff entry — 404 if missing.

**Frontend:** `ApprovalsPage`'s diff entry rendering gains two `<img>` elements (before/after) above the existing old/new text pair, only rendered when the entry has screenshot paths; falls back to text-only exactly as today otherwise (old recordings/heal events with no screenshots keep working unchanged).

**Tests:** `test_verify_heal.py` — `heal_golden_script` populates screenshot paths in `diff_summary` when steps actually drift; a fully on-track heal (no diff) doesn't crash on a missing shot dir. `test_api_heal_list.py` — screenshot endpoint serves a file / 404s appropriately. `ApprovalsPage.test.tsx` — images render when present, hidden when absent.

---

## Task 6: LLM cost/token dashboard

**Problem:** `AzureOpenAiClient.complete()` in `llm/client.py` reads `response.json()["choices"][0]["message"]["content"]` and discards everything else — the same response also contains a `usage: {prompt_tokens, completion_tokens}` block that is never read. Zero token/cost data exists anywhere today.

**Design constraint:** every LLM call in the self-heal path goes through `brain/repair.py::think()`, which is on the do-not-touch list. The single choke point that is *not* on that list is `llm/factory.py::create_llm_client()` — called at all four production entry points (`api.py`'s author/heal endpoints, `cli.py`'s two commands) and nowhere inside `brain/*`. Usage capture is wired entirely through the client object's own optional callback, so `repair.py`, `loop.py`, and every other caller of `llm.complete()` are untouched and see no behavior change.

**Backend:**
- `AzureOpenAiClient.__init__` gains an optional `on_usage: Callable[[dict], None] | None = None` parameter (default `None`, so every existing construction site is unaffected). Inside `complete()`, immediately after `response.json()`, best-effort (wrapped in `try/except Exception: pass`, matching `verify_session_token`'s "never raises" convention so a malformed usage block can never break an existing call): if `self.on_usage`, invoke it with `{"model": target_model, "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}` read from the same already-parsed `response.json()["usage"]`. The method's return value and control flow are unchanged.
- New `db/models/llm_usage.py::LlmUsage` (id, org_id, context_type: `authoring_run`|`heal_event`, context_id, model, input_tokens, output_tokens, cost_usd, created_at) + migration.
- New `engine/src/atlas_engine/llm/usage.py`: `PRICING: dict[str, tuple[float, float]]` (input $/1K tokens, output $/1K tokens; a small static table seeded with the model(s) already configured via `ATLAS_AZURE_OPENAI_MODEL`/`AZURE_OPENAI_MODEL` env vars, `(0.0, 0.0)` fallback for an unrecognized model so cost is simply 0 rather than erroring); `record_llm_usage(org_id, context_type, context_id, **usage) -> None` computes `cost_usd` from `PRICING` and inserts a row; `list_llm_usage_summary(org_id, since=None) -> dict` returns totals plus a per-day breakdown.
- `create_llm_client(*, usage_context: tuple[str, str, str] | None = None)` (org_id, context_type, context_id) gains the param; when given, passes `on_usage=lambda u: record_llm_usage(org_id, context_type, context_id, **u)` into `AzureOpenAiClient(...)`.
- The four call sites (`api.py` ×2, `cli.py` ×2) pass `usage_context` — they already have the org_id and either the `authoring_run_id` or `golden_script_id` in scope at the point `create_llm_client()` is called.
- New `GET /llm-usage/summary?org_id=` endpoint wrapping `list_llm_usage_summary`, guarded by `Depends(require_job_function("developer"))` (Task 4).

**Frontend:** New `frontend/src/pages/dev/CostDashboardPage.tsx` at `/dev/costs` — a totals row (calls, input/output tokens, $ spent) plus a per-day bar chart (following the `dataviz` skill's conventions used elsewhere if any chart exists, otherwise a simple accessible bar list). Add `{ to: '/dev/costs', label: 'Cost dashboard' }` to `NAV.dev`.

**Tests:** `test_llm_client.py` — `on_usage` callback fires with correct token counts on a mocked HTTP response, and a malformed/missing `usage` key doesn't raise or change the returned content. `test_llm_usage.py` — pricing lookup, cost computation, summary aggregation. `test_api_llm_usage.py` — endpoint auth-gating and shape. `CostDashboardPage.test.tsx` — renders totals from a mocked fetch.

---

## Execution order

1. Task 1 (agent library) — pure frontend + one filter param, zero dependencies.
2. Task 2 (request queue) — pure frontend, zero dependencies.
3. Task 3 (title/description/submit) — small schema + service + frontend; Task 2's queue page is what surfaces its output, so do this after Task 2.
4. Task 4 (RBAC) — touches the endpoints Tasks 1–3 added, so do it after they exist to guard them in the same pass rather than twice.
5. Task 5 (heal screenshots) — isolated to `verify/heal.py` + `ApprovalsPage`, independent of 1–4.
6. Task 6 (cost dashboard) — largest, most self-contained new subsystem; last.

Each task ends with the full engine + frontend test suites green before moving to the next.
