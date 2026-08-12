# Recording Requests & Role-Based Authoring Workflow — Design

**Status:** Approved for spec review — pending user sign-off before implementation planning
**Date:** 2026-08-12
**Builds on:** `2026-08-12-atlas-ai-production-architecture-design.md`, `2026-08-12-atlas-ai-database-design.md`, and the Phase 0/1 implementation (`atlas_engine`, currently on `main`).

---

## 1. Context & Problem Statement

Phase 0 and Phase 1 built one linear flow: someone records themselves using a portal, and that same act of recording immediately feeds trajectory extraction → the brain → golden script emission → certification. `author_from_recorded_actions` does both "persist the recording" and "author the script" in a single call.

That flow doesn't match how the product will actually be used. There are two distinct people involved, with two distinct jobs:

- **Operations agents** know the business process (e.g., "how to check eligibility on Payer X's portal") but aren't the ones writing automation. Their job is to demonstrate the manual flow and hand it off.
- **Developers** turn a demonstrated flow into a certified, reliable golden script. They need a queue of open requests, need to see what an ops agent is asking for, and need the actual authoring step to be something they trigger deliberately — not something that happens automatically the instant someone records anything.

Today's code has no concept of a "request," no queue, and no way to distinguish "this recording is a reference demo" from "this recording is the one we're generating a script from." Authoring and recording are the same function call.

## 2. Goals & Non-Goals

**Goals**
- Give operations agents a request-first flow: fill a short form describing what needs automating, then record it, using the exact same manual-record mechanism that already exists.
- Give developers a queue of open requests, and let them either re-record the flow themselves or (in the future, without further code changes) author directly from the ops agent's own recording.
- Split the codebase along this same line: a `recording_service` that owns capture/persistence/requests, and an `authoring_service` that owns trajectory replay/repair/emission/certification. Neither should need to know about the other's internals.
- Keep every existing Phase 0/1 CLI command, API endpoint, and test passing unmodified — this is an additive, backward-compatible change.

**Non-goals (deferred)**
- Voice narration capture and video/screen recording are **not** built in this pass. The schema hooks for them (`video_assets`, `video_steps`) already exist from the original design and are left untouched. This spec only makes sure nothing here would need to change shape when that work starts.
- No new authentication/authorization system. `User.job_function` is a label for driving which UI a person sees, not an access-control mechanism.
- No change to how the brain, action cache, or heal/approve workflow work internally — `authoring_service.author_from_recording` is a straight extraction of logic that already exists in `author_from_recorded_actions`, not a rewrite.

## 3. Data Model Changes

### 3.1 New table: `recording_requests`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `org_id` | uuid FK → organizations | |
| `requested_by` | uuid FK → users | the operations agent |
| `portal_profile_id` | uuid FK → portal_profiles | resolved from `target_url`'s domain at request-creation time via the existing `get_or_create_portal_profile` — a portal profile is guaranteed to exist before anyone records anything |
| `target_url` | text | the URL the ops agent will record against |
| `title` | text | short label, e.g. "Check eligibility — Payer X" |
| `description` | text, nullable | free-text task description |
| `status` | enum: `open`, `submitted`, `fulfilled`, `cancelled` | see §5 for transitions |
| `claimed_by` | uuid FK → users, nullable | developer currently working on it — advisory only, not a lock |
| `claimed_at` | timestamptz, nullable | |
| `fulfilled_golden_script_id` | uuid FK → golden_scripts, nullable | denormalized convenience pointer, same pattern as `GoldenScript.created_from_recording_id` |
| `created_at` | timestamptz | |

### 3.2 `recordings` gains two columns

- `request_id` — uuid FK → recording_requests, nullable. A recording made outside this workflow (e.g. today's ad hoc `atlas record`) has no request.
- `role` — enum: `demo`, `authoring`. Default `demo`. Distinguishes an ops agent's reference walkthrough from a developer's own authoring-source recording *when both exist under the same request* — both are ordinary `Recording` rows, differing only in this tag.

### 3.3 `users` gains one column

- `job_function` — enum: `operations_agent`, `developer`, nullable. Drives which queue/form a person sees in the eventual UI. Not enforced as an access-control boundary in this pass.

### 3.4 Migration approach
One new Alembic migration: `create_table("recording_requests", ...)`, two `add_column` calls on `recordings`, one `add_column` on `users`. No existing column changes, no data migration needed — every new column is nullable or has a default, so this deploys against the existing Phase 0/1 data with zero backfill required.

## 4. Module Split

Today, `atlas_engine/cli.py` directly contains `author_from_recorded_actions`, which both persists a recording and runs the authoring pipeline. This spec splits that:

```
atlas_engine/
├── recording_service.py     # NEW — owns:
│   │                          persist_recording(org_id, start_url, actions, *, request_id=None, role="demo") -> recording_id
│   │                          create_recording_request(org_id, requested_by, target_url, title, description) -> request_id
│   │                          list_recording_requests(org_id, status=None) -> list[dict]
│   │                          claim_recording_request(request_id, developer_user_id) -> None
│   └─                        (capture itself — CaptureSession, the live headed browser — is still driven by cli.py's `record` command; this module owns what happens to the result)
├── authoring_service.py      # NEW — owns:
│   │                          author_from_recording(recording_id, *, llm=None) -> dict
│   └─                        (the brain-replay/emit/certify logic, extracted verbatim from today's author_from_recorded_actions second half)
├── cli.py                    # MODIFIED — thin: record/author-from-actions/request-recording/
│                                list-requests/author commands call into the two services above
└── api.py                    # MODIFIED — same endpoints, same thinning
```

`author_from_recorded_actions` (today's function, and its CLI command / API endpoint) **stays**, for backward compatibility, but its body becomes a two-line wrapper: call `recording_service.persist_recording(...)` to get a `recording_id`, then call `authoring_service.author_from_recording(recording_id, llm=llm)`. Every Phase 0/1 test that calls it keeps working unmodified, because the combined behavior is identical — it's now composed from two functions instead of implemented inline.

## 5. End-to-End Workflow

**Operations agent:**
1. `atlas request-recording --org-id <id> --requested-by <user-id> --target-url <url> --title "..."` → `RecordingRequest(status="open")`, portal profile resolved immediately.
2. `atlas record <url> --org-id <id> --request-id <id> --role demo` → same manual-record flow as today, now tagged to the request → `Recording(role="demo", request_id=...)` → request becomes `status="submitted"`.

**Developer:**
3. `atlas list-requests --status submitted` → queue.
4. Today: re-records their own walkthrough — `atlas record <url> --org-id <id> --request-id <id> --role authoring` → `Recording(role="authoring", request_id=...)`.
   Future (no code change needed): skip straight to step 5 using the ops agent's own `demo` recording's id.
5. `atlas author <recording-id> [--llm ...]` → `authoring_service.author_from_recording` → `GoldenScript(created_from_recording_id=<recording-id>)`. If the resulting script reaches `status="certified"` **and** that recording has a `request_id`, the request is marked `fulfilled` and `fulfilled_golden_script_id` is set. A `draft` result leaves the request `submitted` — it isn't fulfilled by an unproven script.

## 6. CLI / API Surface Changes

New CLI commands: `atlas request-recording`, `atlas list-requests`, `atlas claim-request`, `atlas author` (replaces the recording-creation half of `author-from-actions` for the new flow; `author-from-actions` itself is unchanged/kept).
New API endpoints: `POST /recording-requests`, `GET /recording-requests`, `POST /recording-requests/{id}/claim`, `POST /recordings/{id}/author`.
`atlas record` gains optional `--request-id` and `--role` flags; both default to today's behavior (`request_id=None`, `role="demo"`) when omitted, so the existing command's existing tests need no changes.

## 7. Open Questions / Risks

- **`claimed_by` is advisory, not a lock.** Two developers could both start recording against the same open request. Acceptable for the team-scale usage this is built for now; a real claim/lock would be a Phase-3-scale concern.
- **What happens to a `submitted` request whose demo recording is bad** (ops agent made a mistake) is not designed here — for now, a developer can simply record their own `authoring`-role recording under the same request regardless of the demo recording's quality, so a bad demo doesn't block anything, it's just an imperfect reference.

## 8. References
- `2026-08-12-atlas-ai-production-architecture-design.md` §6.3 (zero-config portal profiles), §8 (video/voice KB — deferred here, hooks preserved).
- `2026-08-12-atlas-ai-database-design.md` (the 14-table baseline this adds one table and three columns to).
