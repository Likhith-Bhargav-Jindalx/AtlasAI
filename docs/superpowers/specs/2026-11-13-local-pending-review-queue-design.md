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
- Fold in a fix for a separate, smaller stop-time risk: `stop()`'s browser
  teardown (video flush, close) can still hang if the browser itself is
  frozen, independent of anything Azure-related.

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
                          # request_id, duration_seconds, action_count,
                          # upload_error (set only after a failed Upload attempt)
  actions.json
  trajectory.json
  codegen.py              (optional)
  video.webm
  video-page{N}.webm      (optional, multi-tab)
  actions-page{N}.json    (optional, multi-tab)
```

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

## Folded-in fix: frozen-browser teardown timeout

Separate from Azure: `stop()`'s teardown still calls into the live browser
(`page.title()` health checks in `_wait_for_stop_or_disconnect`,
`context.close()` in `finalize_video`, `browser.close()`) to flush video to
disk. These are real Playwright calls that can hang if the browser is
frozen (not dead — a dead browser already fails fast via the existing
disconnect-detection path). This is unrelated to anything above and isn't
fixed by removing the Azure call from Stop, since local-only writing still
requires the video to have been flushed first.

Fix: wrap these specific calls with a short, explicit wall-clock guard
(Playwright's sync API doesn't expose a per-call timeout for `title()` or
`close()`, so this needs a small helper — e.g. running the call via a
`ThreadPoolExecutor` future and calling `.result(timeout=5)`). On timeout,
treat it the same as the existing "browser disconnected" path: proceed with
whatever's already flushed to disk rather than silently consuming most of
`stop()`'s overall budget waiting on a frozen process.

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
- **Frozen browser during teardown** — guard times out, proceeds with
  whatever was flushed before the hang (may mean a partial/missing video in
  the pending folder, same risk that exists today if a browser dies mid-
  teardown; actions/trajectory are unaffected since they come from
  already-in-memory capture data, not a live browser call).

## Testing

- **Unit**: manifest read/write; list/get/upload/discard endpoint handlers
  against a temp `ATLAS_RUN_ROOT`.
- **Integration**: Stop produces a `pending_recordings/<uuid>/` folder with
  the expected files, with no DB row created and no Azure Blob client
  invoked (assert `_with_retry`/the blob client are never called from this
  path). Upload runs `persist_recording`, the DB row and `VideoAsset`s
  appear, the pending folder is removed. Discard removes the folder and
  touches nothing else.
- **Frozen-browser guard**: a test that makes `page.title()`/
  `context.close()` block past the short guard timeout and asserts `stop()`
  proceeds instead of hanging out to its outer timeout.
- **Frontend**: `PendingRecordingsPage`/`PendingReviewPage` tests mirroring
  the existing `AgentLibraryPage.test.tsx`/`ReviewPage.test.tsx` patterns;
  `LiveRecordingPage` test updated for the new navigation target.
