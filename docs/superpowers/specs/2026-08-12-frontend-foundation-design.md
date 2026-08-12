# Enterprise Frontend Foundation — Design

**Status:** Approved — proceeding to implementation planning
**Date:** 2026-08-12
**Builds on:** the full `atlas_engine` backend (Phase 0, Phase 1, recording-requests workflow), all on `main`.

---

## 1. Context & Problem Statement

Everything built so far — capture, the brain, codegen, certification, healing, the request/queue workflow — is only reachable via the CLI and raw HTTP calls. There is no UI. This spec designs the frontend that makes the existing backend usable by actual operations agents and developers, plus the two backend gaps that surface the moment a human (not a test) is expected to drive it: there's no login, and there's no way to start/stop a recording from anything other than a blocking terminal `input()` call.

## 2. Goals & Non-Goals

**Goals**
- Stand up a React/Vite/TypeScript/Tailwind frontend with an enterprise-SaaS visual language, in a new top-level `frontend/` directory.
- Add just enough backend to make the frontend real: a login/session mechanism, and start/stop endpoints for live recording.
- Ship three navigable sections — Recordings, Authoring, Approvals — covering the full loop already proven in the backend: submit a request → record → claim → author → certify → heal → approve.
- Every tab is visible to every logged-in user for now; per-role action-gating is explicitly deferred (see Non-Goals).

**Non-Goals (deferred)**
- Full multi-tenant auth (the architecture spec's eventual design) — this is a minimal, real, but not-yet-complete login: password-based, one org's users, no signup flow, no password reset, no per-tab access control.
- Video/voice recording and playback — still just the captured action list, per the original phasing.
- The desktop EXE shell — this frontend is built to become that shell's UI later, but packaging it isn't part of this work.
- Any cloud deployment — this runs as a local Vite dev server talking to the local FastAPI backend, exactly like the rest of this project so far.

## 3. Backend Prerequisite: Minimal Auth

`users.password_hash` has existed since Phase 0 and has never been used. This adds:

- **`atlas create-user`** (CLI, dev-only — no signup flow yet): `--org-id --email --password --role --job-function` → hashes the password with `bcrypt` and creates the `User` row.
- **`POST /auth/login`** (body: `email`, `password`) → verifies against `password_hash`, sets an `httpOnly` signed cookie encoding `{user_id, org_id, role, job_function}`, returns the same as JSON.
- **`POST /auth/logout`** → clears the cookie.
- **`GET /auth/me`** → decodes the cookie, returns the current user's identity, or `401` if absent/invalid.

**Explicit scope boundary:** existing endpoints (`/recording-requests`, `/recordings/{id}/author`, `/golden-scripts`, heal endpoints, etc.) are **not** retrofitted to require or read this session in this pass — they keep taking explicit `org_id`/`user_id` in the request body, exactly as today. The frontend reads `/auth/me` once after login and uses those ids when calling everything else. This keeps the auth addition purely additive — zero risk to the already-tested business logic — at the cost of the backend not yet enforcing "you can only act as yourself." That enforcement is real future work, not an oversight.

## 4. Backend Prerequisite: Live Recording Start/Stop

Today, `atlas record` is one blocking CLI call: launch a headed browser, capture, block on `input()`, then persist. A web "Record" button needs the browser to stay open *across two separate HTTP requests* (start, then stop, with a human doing things in between) — which means the Playwright session has to live in a background thread, not inside either request's handler.

```python
class _LiveCaptureSession:
    def __init__(self, org_id, start_url, request_id, role):
        ...
        self.stop_event = threading.Event()
        self.done_event = threading.Event()

    def _run(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto(self.start_url)
            with CaptureSession(page) as capture:
                self.stop_event.wait()          # blocks here until /stop is called
            browser.close()
        self.recording_id = persist_recording(..., capture.actions, ...)
        self.done_event.set()
```

`POST /recordings/start` creates one of these, starts its thread, stores it in a module-level registry keyed by a new `capture_session_id`, and returns immediately. `POST /recordings/{capture_session_id}/stop` looks it up, sets `stop_event`, blocks (with a timeout) on `done_event`, and returns the resulting `recording_id`. The background thread is the *only* thing that ever touches the Playwright objects — the request thread only ever signals it via `threading.Event`, never calls into Playwright directly — which avoids the exact sync-API deadlock class that Phase 0's capture recorder already hit once (§ see `engine/src/atlas_engine/capture/recorder.py`'s design note). FastAPI endpoints in `api.py` are already plain `def` (not `async def`) throughout, so they already run in Starlette's thread pool — the blocking `done_event.wait()` inside `/stop` doesn't block anything else.

This is a local, single-machine, one-recording-at-a-time-per-session registry — consistent with "local-first" and with the scale this is built for right now.

## 5. Information Architecture

```
/login
/recordings                  — ops agent home
  /recordings/:id            — a recording's captured step list
/authoring                   — developer home
  /authoring/requests/:id    — one request's detail (claim, record, author)
  /authoring/scripts/:portalProfileId  — version history for a portal
/approvals                   — heal-event queue
  /approvals/:healEventId    — diff view, approve/reject
```

All four top-level routes require a session (redirect to `/login` if `GET /auth/me` 401s) but are otherwise visible to anyone logged in — no role-based route guards yet, per the Non-Goals above.

- **Recordings** (ops agent's home): a form to submit a new request (target URL, title, description) → creates the request and immediately offers "Record now"; a Record/Stop button pair driving the new start/stop endpoints; a list of your requests with status pills; a recording detail view showing its captured steps as an ordered list (selector, kind, value where present) — no video yet.
- **Authoring** (developer's home): a queue table of requests (filterable by status), a claim action, a "Record your own walkthrough" flow reusing the same Record/Stop UI tagged `role=authoring`, an "Author" action once a recording exists, and a golden-script list per portal showing every version with its status pill and a "healed from"/"supersedes" lineage link.
- **Approvals**: a table of `pending_approval` heal events, a detail view rendering `diff_summary` as an old-selector → new-selector list per step, and Approve/Reject buttons.

## 6. Visual Design Direction

Enterprise SaaS shell: persistent left sidebar (the three sections + user/org identity + logout), top bar with page title and primary action. Data-dense tables are the default pattern (requests, scripts, heal events all render as sortable/filterable tables, not cards). One consistent status-pill visual language reused across all three state machines already in the backend (`recording_requests.status`, `golden_scripts.status`, `heal_events.status`) — same pill shape and semantic-color mapping (neutral/open, blue/in-progress, green/success, red/rejected) everywhere, so a user learns the pattern once. A real token system (palette, type scale, spacing) gets designed at implementation time, not defaulted to a generic AI-tool look.

## 7. Data Layer

TanStack Query, one hook module per resource, mapped directly onto existing REST endpoints (`useRecordingRequests`, `useGoldenScripts`, `useHealEvents`, etc.) plus the two new ones (`useAuth`, `useLiveRecording`). No new backend list endpoints are needed beyond what Phase 0/1/recording-requests already built, except where noted (live recording start/stop; auth).

## 8. Open Questions / Risks

- **The background-thread capture registry is in-memory and per-process.** Restarting the FastAPI dev server mid-recording loses that session (the headed browser stays open but orphaned). Acceptable for local dev-scale use now; a real fix (persisting session state, or at least detecting orphans) is future work.
- **No route-level or action-level authorization yet.** Anyone logged in can claim any request, approve any heal event, regardless of `job_function`. This is a deliberate, temporary gap per the Non-Goals — flagged so it isn't mistaken for an oversight later.
- **Golden-script "version history per portal" UI** assumes one portal profile maps cleanly to a list of its golden script versions — this is already true in the schema (`golden_scripts.portal_profile_id` + `version`), just noting it as the query this view is built on.

## 9. References
- `2026-08-12-atlas-ai-production-architecture-design.md` §12 (frontend direction, previously deferred to this pass).
- `2026-08-12-recording-requests-and-role-workflow-design.md` (the workflow this UI exposes).
- `engine/src/atlas_engine/capture/recorder.py` (the sync-API deadlock lesson this design explicitly avoids repeating).
