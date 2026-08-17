# Audio Recording + AI-Generated Narration — Architecture Design

Status: proposed, not yet implemented. Supersedes the user's draft plan of the same
name with corrected tool choices (verified mid-2026) and a design that reuses this
codebase's existing schema and orchestration pattern instead of inventing new ones.
§6/§8 updated 2026-08-18 to reflect the desktop exe actually shipped since this was
first written (PyInstaller + pywebview, direct remote Postgres/Blob — no embedded
DB, no Tauri) — see those sections for what changed.

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

## 6. Packaging as a Windows `.exe` — RESOLVED, superseded by what actually shipped

*(Original section preserved in git history. Everything below reflects the
real, already-working desktop exe, built and verified after this doc was
first written — the packaging questions this section originally raised are
answered by what exists now, not by the Tauri/embedded-Postgres plan below.)*

**What actually shipped:** `desktop/app.py`, packaged with **PyInstaller
(`--onedir --windowed`) + pywebview** — not Tauri, no sidecar process, no
separate shell/backend IPC. One frozen Python process runs the existing
FastAPI app in-process and shows the existing React frontend in a native
webview window. Both a macOS and a Windows build exist and have been run
and verified end to end (real login, real recording, real certification)
against production infrastructure.

**Postgres — resolved, and much simpler than this section originally
assumed.** No embedded Postgres binary, no SQLite migration. The exe
connects **directly to a real, network-reachable Azure Postgres Flexible
Server** using a connection string baked into the build at compile time
(`desktop/production.env`, gitignored, `--add-data`'d into the PyInstaller
bundle). There is no local database of any kind on the end user's machine.
This sidesteps the entire "no Postgres on the end user's machine" problem
this section raised — there's nothing to embed or migrate, because the app
was never meant to run offline against a local DB in the first place.

**Chromium bundling — resolved.** `PLAYWRIGHT_BROWSERS_PATH=0` vendors
Chromium inside the `playwright` package itself at build time
(`desktop/build.sh` / `.github/workflows/build-exe.yml`), shipped
pre-downloaded inside the installer. No first-run download step, no network
dependency at launch — matters for the same locked-down-healthcare-desktop
concern this section originally flagged.

**Blob storage** works the same way as Postgres: a real Azure Storage
connection string baked in at build time, no local storage backend.

**What this means for the narration feature specifically:** none of the
packaging risk this section originally worried about applies to narration
either. The new pipeline (ffmpeg, faster-whisper, kokoro-onnx) bundles into
the *same* existing PyInstaller build the same way Playwright already does
— add the dependencies, add `--add-data`/`--add-binary` entries for any
non-Python assets (the Kokoro ONNX weights, a static ffmpeg binary), and the
existing `desktop/build.sh`/CI workflow picks them up. This is additive to
an already-solved packaging problem, not a new one.

**Ollama still can't be bundled** — that part of the original analysis
holds regardless of shell/packaging choice (Ollama itself is ~3.25GB on
Windows; a vision model adds several more GB, and this is a known,
discussed limitation of Ollama-based apps generally, not specific to this
one). It stays a separate, optional install: detect `localhost:11434` at
startup, and if absent, disable the "AI Generated" toggle with a one-line
"Install Ollama to enable AI narration" prompt linking to ollama.com. Raw
playback, transcription, and narration-script-without-vision-context all
keep working with zero degradation if Ollama is absent.

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

1. ~~Postgres packaging approach~~ — **resolved** (§6): the shipped exe
   connects directly to a real remote Postgres, nothing to embed.
2. **Default narration LLM when no Azure key is configured** — still open.
   Local Ollama text model (fully free, matches the "free tools only"
   constraint) vs. requiring Azure for this one step. Recommend Ollama-first
   per the stated constraint, and because Ollama is already the required
   dependency for the vision step (§4.3) on any machine where narration is
   fully enabled.
3. ~~Chromium pre-bundled vs. fetched on first launch~~ — **resolved** (§6):
   already pre-bundled in both shipped exes.
