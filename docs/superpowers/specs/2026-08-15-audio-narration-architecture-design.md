# Audio Recording + AI-Generated Narration — Architecture Design

Status: proposed, not yet implemented. Supersedes the user's draft plan of the same
name with corrected tool choices (verified mid-2026) and a design that reuses this
codebase's existing schema and orchestration pattern instead of inventing new ones.

## 1. Goal

On a recording's page, add a toggle next to the existing video/steps view:

- **Raw** — plays back the actual microphone recording captured while the agent
  narrated their actions live.
- **AI Generated** — plays a fully synthesized voiceover that:
  - understands the video's visual context (what's on screen at each step),
  - understands everything the agent said in the raw recording,
  - re-narrates it as a clean, professional voiceover,
  - stays frame-synced — narration for step N is audible while step N is on screen,
  - preserves every piece of information the agent shared (no summarizing-away detail).

Constraints from the user: every tool in the pipeline must be free/open-source; any
amount of complexity is acceptable to hit the quality bar; the whole app should
eventually ship as a Windows `.exe`.

## 2. Reuse what already exists — don't reinvent

Before designing anything new, the schema already has almost this exact feature
half-built and unused:

```python
# engine/src/atlas_engine/db/models/video.py
VIDEO_ASSET_KINDS = ("raw", "narrated_edit")   # "narrated_edit" is already the AI-narrated slot
VIDEO_ASSET_STATUSES = ("processing", "ready", "failed")

class VideoAsset(Base):
    kind: Mapped[str]              # "raw" | "narrated_edit"
    storage_ref: Mapped[str]
    transcript_ref: Mapped[str | None]
    status: Mapped[str]            # processing/ready/failed — a job-status column, already there

class VideoStep(Base):
    offset_ms: Mapped[int | None]              # per-step timeline position — already there
    narration_audio_ref: Mapped[str | None]    # per-step synthesized audio — already there, unused
    narration_transcript: Mapped[str | None]   # per-step narration text — already there, unused
    duration_ms: Mapped[int | None]            # per-step duration budget — already there, unused
```

```python
# engine/src/atlas_engine/db/models/portal.py
class Recording(Base):
    has_audio: Mapped[bool] = mapped_column(default=False)   # already there, always False today
```

This means: **no new tables, and almost no new columns.** The feature is "populate
existing columns via a new pipeline," not "design a new data model." The two real
schema changes needed:

1. `Recording.has_audio` → set `True` when a mic track was captured.
2. A new `VideoAsset.narration_source` enum-ish text column (`"raw_mic"` /
   `"ai_generated"`), OR simply: the existing `kind="raw"` VideoAsset's own audio
   track *is* "Raw", and a second `VideoAsset(kind="narrated_edit")` row *is* "AI
   Generated" — one recording, two VideoAsset rows, frontend toggle just switches
   which `video_asset_id` it points the player at. This needs zero new columns.

**Recommendation: option 2.** It matches the grain the schema was clearly designed
at (one row per renderable video variant) and the frontend toggle becomes "pick
which sibling VideoAsset to play," which is a query-param switch, not new state.

## 3. Orchestration — reuse the existing job pattern, don't build a new one

This codebase already has this exact shape of problem solved twice:
`_AuthoringRunSession` (`engine/src/atlas_engine/authoring/run.py`) and
`_LiveCaptureSession` (`engine/src/atlas_engine/live_capture.py`) both use:

- an in-memory session object owning a background thread,
- a DB row as the source of truth (`status`, `current_phase`) that the frontend
  polls on an interval (`useAuthoringRun`'s `refetchInterval: 1500`),
- an event log table for step-by-step progress messages,
- a `stop_*` function that can cancel the in-memory session or reconcile an
  orphaned DB row if the process restarted mid-run (the fix just verified in
  this session).

The narration pipeline should be a **third instance of the same pattern**:
`_NarrationRunSession`, keyed off `VideoAsset.status` (already
processing/ready/failed) instead of a new status enum, with a `NarrationRunEvent`
log table mirroring `AuthoringRunEvent`. This is a strictly smaller lift than
authoring — no phases like replay/brain/certify, just a linear pipeline — but the
DB-status-row + polling + event-log skeleton should be copied, not redesigned.

Concretely: `POST /recordings/{id}/narration` starts it, `GET
/video-assets/{id}` (or extend the existing recording-detail response) exposes
`status` for the frontend to poll, same as `useAuthoringRun`.

## 4. Pipeline stages

### 4.1 Mic capture (raw audio)

Capture audio via `ffmpeg` (already a Playwright dependency — bundled at
`~1.3 MB` alongside the Chromium download) against the system default input
device, started at `CaptureSession` init time.

**Critical correction vs. the user's draft:** don't derive audio/video sync from
`audio_started_at_ms - video_started_at_ms` measured as two independent wall-clock
reads in two different processes (the ffmpeg subprocess and the Playwright
process). Subprocess spawn latency (10s–100s of ms, unpredictable under load) sits
directly in that delta and will drift the whole track. Instead: reuse the *same*
monotonic clock this codebase already timestamps `VideoStep.offset_ms` from (the
capture session's own clock, per `capture/recorder.py`), and stamp "mic recording
armed" as one more event on that same timeline. Sync then falls out of using one
shared clock instead of reconciling two.

### 4.2 Speech-to-text — **faster-whisper**

- MIT-licensed CTranslate2 reimplementation of OpenAI Whisper — free, no API key.
- `word_timestamps=True` gives word-level start/end times, needed to know which
  words the agent said during which step's on-screen window.
- CPU-only is viable: the tiny/base models run faster than real-time on any
  modern CPU; even with `int8` quantization on a mid-size model, a 13-minute clip
  transcribes in the ~2–3 minute range on CPU alone — acceptable for an
  after-the-fact background job.
- No PyTorch dependency (CTranslate2 is its own lightweight inference engine) —
  matters for the `.exe` size budget below.

### 4.3 Visual context — Ollama + a local vision-language model

The recording pipeline already captures a `screenshot_ref` per `VideoStep` (see
§2) — there is no need to re-extract frames from the video file; the per-step
screenshots already are the frame set to analyze.

- Run via **Ollama** (already the natural choice for "free, local, swappable
  models"), pointed at each step's screenshot.
- Model recommendation: **`gemma4:e4b`** — Google DeepMind's Gemma 4 family
  (Apache-2.0, released April 2026, requires Ollama ≥0.22), the on-device-default
  "Effective 4B" tag, ~4.5GB at Q4 quantization. Gemma 4 ships native vision
  support and is strong at reading dense on-screen text and UI chrome, exactly
  what an eCW/portal screenshot needs described. `gemma4:12b` is a viable
  quality upgrade on machines with headroom (~6.7GB) — make the model name a
  config value, not a hardcoded choice.
- Output per step: a short structured description ("Dashboard page, 'Generate
  Report' button highlighted, a form with 4 fields visible: ...").

### 4.4 Narration script generation

One LLM call per step (or per small window of steps, to preserve continuity),
given:
- the step's own caption/`dom_action_event` (already captured, deterministic),
- the vision model's description of that step's screenshot (§4.3),
- the raw speech transcript segment whose word timestamps fall inside that
  step's `offset_ms` → next step's `offset_ms` window (§4.2),
- the step's available duration budget (`next.offset_ms - this.offset_ms`).

Prompted to preserve every fact the agent stated while writing it as clean
narration, and told the target speaking duration so the model doesn't
overshoot before TTS even runs.

This reuses `llm/factory.py::create_llm_client()` (Azure OpenAI, already wired
this session with cost tracking via `on_usage`) if configured — or, to honor
"everything must be free," this is exactly the kind of call that should default
to a local Ollama **text** model when no Azure key is present, with Azure only
as an optional upgrade path. Add this as a second branch in `create_llm_client`.

### 4.5 Text-to-speech — **Kokoro-82M**, via the ONNX build

- Apache-2.0 licensed, 82M-parameter model — genuinely free for commercial use,
  no attribution requirements beyond the license text.
- Quality-for-size is the best available in the open-source tier: it topped the
  TTS Arena in its class, ahead of much larger open models.
- Runs at roughly **6× real-time on a CPU with no GPU** — a 3-second narration
  segment synthesizes in ~0.5s, fast enough to run per-step in a background job
  without becoming the pipeline bottleneck.
- Use the **`kokoro-onnx`** package (ONNX Runtime), not the reference PyTorch
  package — same model weights, ~80MB quantized, and critically **avoids a
  PyTorch dependency entirely**, which matters a lot for the `.exe` size budget
  (§6).
- Caveat to plan around: Kokoro ships preset voices only (no voice cloning) and
  an 8-language range — fine for this use case (a consistent narrator voice
  across all recordings), just don't design toward "clone the agent's own
  voice," which isn't what this model does.

### 4.6 Segment-duration fitting

Kokoro's raw output for a script line will rarely match the step's exact
available window. Fitting strategy, in order of preference (cheapest/highest
quality first):

1. **Re-synthesize at an adjusted speed** — Kokoro accepts a speed parameter;
   if the first pass is within ~±20% of the target duration, resynthesize once
   at `target_duration / actual_duration` speed. This avoids any post-processing
   artifact entirely.
2. **Time-stretch, not re-synthesize, for small residual mismatch** (±10%) —
   use `audiostretchy` (built specifically for speech, and explicitly tuned for
   the 0.9–1.1x stretch range) rather than raw `ffmpeg atempo`. Plain `atempo`
   is fine for casual use but audiostretchy/rubberband-family algorithms sound
   materially better on voice for the same stretch ratio, and this is a
   quality-sensitive narration track, not a rough scratch dub.
3. **Trim trailing silence or pad with silence** for anything still off after
   steps 1–2 — never let a step's narration bleed into the next step's window;
   log a warning when a step's script had to be shortened, so the LLM-prompt
   duration budget in §4.4 can be tuned over time.

### 4.7 Assembly and sync

Build the full narration track as a numpy buffer: place each step's finished
segment at its `offset_ms`, silence everywhere else. This is a direct reuse of
timestamps this codebase already stores — no new sync math beyond what §4.1
already established (one shared clock, not two reconciled ones). Mux the
resulting WAV into the video with `ffmpeg` as `VideoAsset(kind="narrated_edit")`,
`transcript_ref` pointing at the assembled script.

## 5. API and frontend surface

- `POST /recordings/{id}/narration` — starts the pipeline (Task per §3).
- `GET /recordings/{id}` response gains a `narrated_video_asset_id` alongside
  the existing raw one; frontend toggle is two radio options bound to
  `recordingVideoUrl(recordingId, pageIndex, variant)`, defaulting to "Raw" and
  falling back to it (with the toggle disabled/greyed) whenever
  `narrated_video_asset_id` is null or still `processing`.
- Poll narration status the same way `useAuthoringRun` already polls — a
  `refetchInterval` on the recording-detail query while status is `processing`.

## 6. Packaging as a Windows `.exe`

**Recommended shell: Tauri (not Electron), with the Python/FastAPI backend
bundled as a PyInstaller-compiled sidecar binary.** This is an established,
templated pattern (multiple public Tauri-v2 + FastAPI sidecar templates exist)
rather than something novel: Tauri's Rust shell spawns the PyInstaller'd
`atlas-engine.exe` as a child process on a localhost port, the existing React
frontend loads inside Tauri's native webview, and the two talk over the same
`apiFetch` HTTP calls already used today — no frontend rewrite. Tauri over
Electron specifically because the installer overhead here is already large from
the Python/ML stack; Tauri doesn't also ship a second Chromium (Electron does),
so it doesn't compound the size problem.

**Dependency budget, and what's actually feasible to bundle:**

| Dependency | Size | Bundle it? |
|---|---|---|
| Playwright + Chromium | ~500MB (Chromium ~183MB + headless shell ~113MB + deps) | Yes — set `PLAYWRIGHT_BROWSERS_PATH` into the install dir, download during the installer's first-run step rather than inflating the download itself, OR ship pre-downloaded (bigger installer, zero first-run wait) |
| faster-whisper (CTranslate2) | tens of MB, no PyTorch | Yes, straightforward |
| kokoro-onnx | ~80MB model + ONNX Runtime, no PyTorch | Yes, straightforward |
| ffmpeg | a few MB (static binary) | Yes, trivial |
| **Ollama + a vision model** | Ollama itself ~3.25GB on Windows; a 4B vision model adds another few GB | **No — do not bundle.** Confirmed too large to ship inside an app installer (this is a known, discussed limitation even for apps built around Ollama specifically). Ship as an optional, separately-installed dependency: detect `localhost:11434` at startup, and if absent, disable the "AI Generated" toggle with a one-line "Install Ollama to enable AI narration" prompt linking to ollama.com. Raw playback, transcription, and everything else keeps working with zero degradation. |
| Postgres | N/A | **Open decision, not yet resolved** — see below |

**Postgres is the biggest unresolved packaging question**, bigger than the
audio feature itself: today the app assumes a running Postgres (docker-compose,
port 5434). A `.exe` end user has neither Docker nor Postgres. Two real options,
not a fake one:
- Bundle a real embedded Postgres binary (e.g. via a "postgresql-embedded"-style
  approach that downloads/ships the actual `postgres.exe` and runs it as a
  local subprocess against a data directory inside the install folder) — keeps
  100% schema/query compatibility, adds installer size and a first-launch
  init step, but zero application-logic changes.
- Add a single-user SQLite mode as an alternate `engine` backend — smaller
  installer, no subprocess to manage, but touches every SQLAlchemy model for
  dialect differences (this codebase uses Postgres-specific types in places)
  and is real, non-trivial engineering, not a drop-in.

Given the standing rule this session has followed all along — never change
working logic without a specific, approved reason — the embedded-Postgres-binary
path is the one consistent with that rule; the SQLite path is a genuine
architecture change and should only be chosen if raised and approved
separately, not folded into this feature's scope.

**Rough installer size estimate:** ~650–750MB pre-downloaded (Playwright/Chromium
~500MB + embedded Postgres binary ~30–50MB + faster-whisper/kokoro-onnx/ffmpeg
~150MB + the Tauri shell + PyInstaller'd backend itself, tens of MB) — comparable
to a modern browser installer, before Ollama (which stays a separate, optional
download entirely, so it does not count against this number).

**The 3 biggest realistic risks:**
1. **Postgres packaging is real, unstarted work**, independent of and larger
   than the narration feature — it's the actual blocker for "ship as .exe," not
   anything audio-related. Should be scoped as its own spec before committing to
   a `.exe` date.
2. **Ollama can't be bundled**, so "AI Generated narration" is a soft feature on
   the packaged app — present but off until the user separately installs Ollama.
   That needs to be an explicit, designed empty/disabled state, not an
   afterthought.
3. **Playwright browser installs are large and OS/version-pinned** — a Windows
   installer needs to either ship the matching Chromium build inline (simplest,
   biggest download) or run `playwright install` at first launch (needs network
   access at install time, which some target environments — e.g. locked-down
   healthcare desktops, relevant given this app's eCW usage — may not allow).

## 7. Sources

- [Tauri vs Electron 2026 comparison](https://blog.nishikanta.in/tauri-vs-electron-the-complete-developers-guide-2026) — bundle size / security tradeoffs
- [Tauri v2 + FastAPI sidecar template](https://github.com/fudanglp/tauri-fastapi-full-stack-template)
- [Tauri + Python PyInstaller sidecar walkthrough](https://aiechoes.substack.com/p/building-production-ready-desktop)
- [Playwright browser download sizes](https://playwright.dev/python/docs/browsers)
- [postgresql_embedded crate](https://crates.io/crates/postgresql_embedded) — embedded-Postgres-binary approach
- [Ollama Windows release size (~3.25GB) too large to bundle](https://github.com/ollama/ollama/issues/7419)
- [Kokoro TTS license, quality, speed](https://kompozy.io/reviews/kokoro-tts)
- [Kokoro-82M-ONNX weights, no-PyTorch runtime](https://huggingface.co/onnx-community/Kokoro-82M-ONNX)
- [faster-whisper MIT license, CPU performance, word timestamps](https://github.com/AIXerum/faster-whisper)
- [Gemma 4 release: Apache 2.0, native vision, Ollama tags](https://cloud.google.com/blog/products/ai-machine-learning/gemma-4-available-on-google-cloud)
- [Gemma 4 Ollama tags and VRAM requirements (e2b/e4b/12b/26b/31b)](https://www.theaitechpulse.com/gemma4-ollama-guide-2026)
- [audiostretchy tuned for speech time-stretch ratios](https://pypi.org/project/audiostretchy/1.3.1)

## 8. Open decisions for the user

1. Postgres packaging approach (§6) — embedded binary vs. a separate SQLite
   architecture effort. Recommend deciding this before scheduling `.exe` work,
   since it's independent of and larger than the narration feature.
2. Default narration LLM when no Azure key is configured — local Ollama text
   model (fully free, matches the "free tools only" constraint) vs. requiring
   Azure for this one step. Recommend Ollama-first per the stated constraint.
3. Whether to ship Playwright's Chromium pre-bundled in the installer (bigger
   download, zero first-run wait, works offline) or fetched on first launch
   (smaller installer, needs network + may be blocked on locked-down machines).
