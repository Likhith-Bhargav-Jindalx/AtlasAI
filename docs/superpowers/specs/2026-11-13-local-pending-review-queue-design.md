# Local pending-review queue for recordings

## Problem

Today, clicking "Stop and review" on a live recording synchronously runs
`persist_recording()` inside the capture's background thread before the
agent sees anything: it writes `actions.json`/`trajectory.json`/
`codegen.py`/`video.webm` to local disk **and** uploads each of them to
Azure Blob, then creates the `Recording` row in the shared Postgres DB.

A confirmed production incident (DB + Sentry evidence, 2026-11-12) showed
this upload step failing outright on a dropped connection
(`ServiceResponseError`/write-timeout), exhausting the upload retry budget,
and leaving `Recording.status = "failed"` with no `VideoAsset` row at all —
from the agent's perspective, the whole recording session was lost, even
though the locally-captured actions/video bytes briefly existed on disk.

A first fix (merged, PR #21) made the upload retry harder (3→6 attempts,
~31s total) and stopped deleting the local staging copy on failure. This
spec goes further: it removes the Azure upload (and the Postgres row
creation) from the Stop path entirely, so an upload problem can never again
cause a recording to be lost or marked failed.

## Goals

- Clicking Stop never blocks on, or can be broken by, a network call to
  Azure Blob or Postgres.
- The captured recording is never lost due to an upload failure. The agent
  explicitly decides, after reviewing, whether to upload or discard it.
- A pending (not-yet-uploaded) recording is visible only on the machine
  that recorded it — no other agent, and no shared list, can see it until
  it's uploaded.

## Non-goals

- `/recordings/submit-capture` (the multipart endpoint used when the engine
  runs as a remote deployed backend rather than locally on the agent's
  machine) is untouched. This spec only changes the local live-capture path
  (`LiveRecordingPage.tsx` → `live_capture.py`).
- No automatic background retry/upload. Upload is a manual, agent-triggered
  action only — this was an explicit product decision, not an oversight.
- No change to the existing submit-for-authoring flow, Section B form, or
  anything downstream of a recording actually being uploaded — all of that
  continues to work exactly as it does today, once a recording reaches
  Postgres.
- **The frozen-browser teardown timeout** (`stop()`'s `page.title()`/
  `context.close()`/`browser.close()` calls hanging if the browser is
  frozen rather than dead) was originally folded into this spec, then
  dropped during implementation: Playwright's sync API is thread-affine —
  every call against `page`/`context`/`browser` must run on the exact
  thread that opened the `sync_playwright()` session, so wrapping one of
  these calls in a `ThreadPoolExecutor`-based timeout doesn't bound it,
  it just makes every call fail instantly with
  `greenlet.error: cannot switch to a different thread`. Confirmed with a
  minimal repro and by reverting the attempt (3 existing `live_capture`
  tests that depend on real popup/tab timing started failing the moment
  the guard was added, and passed again once it was reverted). Fixing this
  for real would mean rewriting the capture loop on Playwright's async API
  (where `asyncio.wait_for(..., timeout=...)` works correctly) — out of
  scope here; today's existing outer `stop()` timeout remains the only
  safety net for a frozen browser.

## Architecture

**Today:**
```
Stop click -> stop_live_capture() -> browser teardown -> persist_recording()
                                                           (local write + Azure upload + Postgres row, all synchronous)
```

**New:**
```
Stop click -> stop_live_capture() -> browser teardown -> write to local pending folder only
                                                           (no Postgres, no Azure)

                                      ... agent reviews locally, whenever ready ...

Upload click -> persist_recording()  (today's exact logic, now run on demand)
                -> Postgres row created, Azure upload happens, now visible to everyone

Discard click -> delete the local pending folder. Nothing was ever created anywhere.
```

### Why not create the DB row immediately with a new "pending" status instead?

Considered and rejected. A Postgres row is visible cluster-wide the instant
it exists, regardless of where the actual bytes live — that directly
contradicts the goal that a pending recording must be visible only on the
machine that recorded it. Keeping pending recordings out of Postgres until
Upload also means this feature needs **no Alembic migration** — no new
`recording_status` enum value, no schema change at all.

## Local storage

New directory, independent of the existing `ATLAS_RUN_ROOT/azure-staging`
(introduced in PR #21, which keeps serving its original purpose — staging
for the real upload, now run from the Upload step instead of automatically
during Stop):

```
ATLAS_RUN_ROOT/pending_recordings/<uuid>/
  manifest.json          # org_id, user_id, start_url, created_at, role,
                          # request_id, video_started_at_ms, video_ended_at_ms,
                          # action_count, per-extra-page timing/video info,
                          # upload_error (set only after a failed Upload attempt)
  actions.json
  trajectory.json
  codegen.py              (optional)
  video.webm
  video-page{N}.webm      (optional, multi-tab)
  actions-page{N}.json    (optional, multi-tab)
  extraction-{i}.json     (optional, one per page-data grab during capture)
  trace.zip, network.har  (optional, rarely present — capture_artifacts
                           today's persist_recording already supports)
```

All of the above mirrors exactly what `persist_recording` writes today
(`recording_service.py`), minus the Azure upload and Postgres row — every
kwarg it accepts must have a local-only equivalent here, or that data is
silently lost the moment a recording passes through the pending queue.

`manifest.json` is the only new data model this feature introduces — a
flat local JSON file per pending recording. No ORM model, no DB table.

## Backend API (new; local-only, served by the same engine process)

These routes only ever read/write `pending_recordings/` on local disk. Only
`upload` ever touches Postgres or Azure.

- `GET /pending-recordings` — lists manifests, newest first.
- `GET /pending-recordings/{id}` — one manifest plus its `actions.json`/
  `trajectory.json` content, read and shaped on each request (not cached as
  DB rows).
- `GET /pending-recordings/{id}/video/{page_index}` — streams
  `video.webm`/`video-page{N}.webm` straight off local disk via
  `FileResponse`, the same mechanism `get_recording_video_endpoint` already
  falls back to for local storage today.
- `POST /pending-recordings/{id}/upload` — calls `persist_recording()` with
  the manifest's fields (unchanged logic from today, including PR #21's
  retry/staging behavior). On success: deletes `pending_recordings/<id>/`,
  returns the new `recording_id`. On failure: folder is kept as-is,
  `manifest.json` gets `upload_error` set for display; the agent can retry
  the same button or discard instead.
- `POST /pending-recordings/{id}/discard` — deletes the folder. Nothing is
  created anywhere; this recording never existed outside that laptop.

### Stop endpoint change

`stop_recording_endpoint`/`stop_live_capture`/`_LiveCaptureSession.stop()`
and `._run()` (`live_capture.py`) keep doing browser teardown exactly as
today, but `_run()`'s `persist_fn` call is replaced with a local-only write
(no `AzureRunDir`, no `_with_retry`, no `session_scope()`/Postgres) that
produces the `pending_recordings/<uuid>/` folder described above and
returns its id in place of a `recording_id`.

## Frontend

New nav entry in `AppLayout.tsx`'s `NAV.agent`, alongside the existing two:

```
{ to: '/agent', label: 'Request inbox' },
{ to: '/agent/library', label: 'Recording library' },
{ to: '/agent/pending', label: 'Pending uploads' },   // new, optionally with a count badge
```

- **`/agent/pending`** → new `PendingRecordingsPage.tsx` — same
  grid-of-cards visual style as `AgentLibraryPage.tsx`, backed by
  `GET /pending-recordings` instead of the Postgres-backed `useRecordings`
  hook.
- **`/agent/pending/:pendingId`** → new `PendingReviewPage.tsx` — video
  player (from the local-disk video endpoint above), the raw action list
  from `actions.json`, and two buttons: **Upload** and **Discard**. No
  title/description form and no submit-for-authoring button here — those
  only make sense once a recording is actually in Postgres.
- `LiveRecordingPage.handleStop()` (`LiveRecordingPage.tsx:35`) navigates to
  `/agent/pending/${pendingId}` instead of today's
  `/agent/recordings/${recording_id}`.
- On a successful Upload from `PendingReviewPage`, navigate forward into
  the existing `/agent/recordings/${recording_id}` (`ReviewPage.tsx`) — this
  is the handoff point where the new flow rejoins today's existing one,
  unchanged from here on.

## Error handling

- **Local disk write fails during Stop** (disk full, permissions) — this is
  now the *only* way Stop itself can fail. Surfaced directly, same as any
  other local I/O error; nothing new to design here.
- **Upload fails after retries exhausted** — folder stays in
  `pending_recordings/`, never deleted, `upload_error` shown in the UI,
  agent can retry anytime or discard. Reuses PR #21's retry/staging logic
  unchanged — it just now runs on demand instead of automatically.
- **App restart with items still in `pending_recordings/`** —
  `GET /pending-recordings` just re-lists whatever's on disk. No
  reconciliation needed; no orphaned DB rows are possible since none were
  ever created for a pending item.

## Testing

- **Unit**: manifest read/write; list/get/upload/discard endpoint handlers
  against a temp `ATLAS_RUN_ROOT`.
- **Integration**: Stop produces a `pending_recordings/<uuid>/` folder with
  the expected files, with no DB row created and no Azure Blob client
  invoked (assert `_with_retry`/the blob client are never called from this
  path). Upload runs `persist_recording`, the DB row and `VideoAsset`s
  appear, the pending folder is removed. Discard removes the folder and
  touches nothing else.
- **Frontend**: `PendingRecordingsPage`/`PendingReviewPage` tests mirroring
  the existing `AgentLibraryPage.test.tsx`/`ReviewPage.test.tsx` patterns;
  `LiveRecordingPage` test updated for the new navigation target.
