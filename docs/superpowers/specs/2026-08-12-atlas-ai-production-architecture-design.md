# Atlas AI — Production Architecture Design

**Status:** Approved for spec review — pending user sign-off before implementation planning
**Date:** 2026-08-12
**Author:** Likhith Bhargav (Anka Health), designed with Claude
**Supersedes:** The prototype at `/Users/apple/Desktop/atlasAI` (reference-only from this point forward, not incrementally migrated)

---

## 1. Context & Problem Statement

Over roughly nine days in July 2026, a prototype ("Atlas AI") was built at `/Users/apple/Desktop/atlasAI` that records a human performing a workflow once in a browser (e.g., logging into an EHR portal and pulling a report), and generates a deterministic, LLM-free Playwright script — a "golden script" — that reproduces the workflow reliably. The prototype went through three real architecture generations and converged on a good idea, referred to internally as **"the Brain"**: the recording itself is frozen as ground truth; a step executor replays it exactly; a deterministic evaluator checks whether each step's real-world effect matches what was recorded; and an LLM is invoked *only* as a narrowly-scoped, DOM-validated exception handler when a step goes off-track. The shipped golden script never calls an LLM at runtime.

That idea works, but the system built around it does not hold up as a product:

- It is a Node/TypeScript monorepo that generates entire Python programs as string templates inside `.ts` files and shells out to them — four independent places re-implement the same browser-automation logic, one of them calling an undocumented private Playwright API.
- There is no database. Every run's state is a folder on disk with inconsistent naming conventions; run history lives in an in-memory registry that is wiped on server restart.
- Only one pipeline run can be active in the entire process at a time — a hard concurrency ceiling.
- Every portal requires a hand-written JSON config file before it can be recorded at all.
- There are no automated tests anywhere in the codebase.
- There is no packaged distribution (EXE, installer) — it only runs via `npm run` in a checked-out repo, and browser automation for anything requiring manual MFA cannot run inside the shipped Docker image at all.

The user's goal is to take the working *idea* from that prototype and rebuild it as a real product: a desktop application any user can download and use immediately on any web portal with zero configuration, backed by a proper cloud platform, with a credible path to self-healing automation and a video/voice knowledge-base feature, aimed eventually at a broad multi-tenant market release beyond the current healthcare/EHR customer base.

## 2. Goals & Non-Goals

**Goals**
- Ship a downloadable desktop application (Windows first) that lets a user record themselves performing a workflow on *any* website and receive a certified, deterministic Playwright script — with no manual configuration step.
- Replace all filesystem-as-database patterns with a real, versioned data model (Postgres + object storage).
- Make self-healing a first-class, versioned, auditable capability rather than a silent in-place mutation — appropriate for a product that touches PHI-bearing healthcare portals today and other regulated/sensitive portals later.
- Design the capture layer so the same recording session can, later, produce a narrated video knowledge-base entry — without a second, separate capture mechanism.
- Build on a codebase with real automated tests, since the prototype has none.
- Architect for eventual multi-tenant, market-scale usage, while keeping the first release scoped to an internal team.

**Non-goals (explicitly out of scope for this design)**
- A browser extension is **not** part of the near-term build. It is acknowledged as the right *next* capture mode after the EXE (see §17) but is not designed in implementation detail here.
- Full HIPAA-grade compliance infrastructure (enforced audit logging, signed BAAs beyond what Azure OpenAI already requires, data-residency controls, formal retention policies) is **not** a Phase 0/1 requirement. The data model is shaped to make this addable later without a rewrite, but building it is deferred.
- Hosted/cloud execution of production golden scripts (as opposed to local EXE execution) is **not** required for the initial release. Generating the certified golden script is the product's core deliverable; where the user subsequently runs that script is their choice.
- A polished, final UI design is **not** specified pixel-by-pixel in this document — see §12.

## 3. Guiding Principles

1. **The recording is ground truth, never distilled by an LLM.** This is the single most important lesson from the prototype's own history (its Era 1→2→3 evolution was driven entirely by discovering that LLM-distilled plans are non-deterministic). Every stage downstream of capture operates on the literal recorded trajectory; an LLM may annotate, propose a repair to, or extract structure from it, but never regenerate it from scratch.
2. **No LLM call ever ships inside a golden script.** Golden scripts are plain, auditable Playwright code. All LLM involvement happens at authoring/healing time, is logged, and is removed from the hot path before certification.
3. **Zero-config by default.** A user should never have to author a JSON file before recording a portal. Portal profiles are inferred and refined automatically.
4. **Local-first execution, cloud for judgment and continuity.** The desktop app does real work locally (capture, replay, script generation) so it works with the user's own already-authenticated sessions, VPNs, and on-prem systems. The cloud exists to run LLM calls under the org's license, hold team data, and be the durable store — not to be a hard dependency for every local action.
5. **Every automated change to a live production script is inspectable and, by default, gated by a human before it goes live** — because these scripts touch PHI-bearing and otherwise sensitive systems.
6. **One pipeline, one implementation language.** The prototype's split between a TypeScript orchestrator and four separately-maintained generated-Python emitters is retired. One Python engine implements capture, extraction, the brain, emission, and certification exactly once.

## 4. System Topology

```
┌─────────────────────────────┐        ┌──────────────────────────────────────┐
│  Desktop App (per user)     │        │   Cloud Platform (Azure)              │
│  ──────────────────────     │        │   ───────────────────────             │
│  • Local FastAPI service    │        │  • Multi-tenant API (FastAPI)         │
│  • Python engine:           │  HTTPS │  • Postgres (Flexible Server)         │
│      capture / trajectory / │◄──────►│  • Blob Storage (goldens, video, HAR, │
│      brain / emit / certify │  sync  │    screenshots, trace files)          │
│    (native Playwright,      │   +    │  • Key Vault (secrets, credentials)   │
│     no generated-code glue) │  LLM   │  • Azure OpenAI (all brain/heal/      │
│  • Local SQLite (run state, │ proxy  │    extraction LLM calls)              │
│    offline queue)           │        │  • Web dashboard (React) — team mgmt, │
│  • pywebview shell embedding│        │    run history, video KB, approvals,  │
│    the shared React UI      │        │    on-demand self-heal                │
└─────────────────────────────┘        └────────────────────────────────────────┘
   Recording, authoring, and                Team/org data, licensing, LLM
   local script execution happen             brokering, durable storage,
   here, on the user's own machine           knowledge base, approvals
```

Recording, authoring, and local script execution happen on the user's machine. The cloud platform is the system of record for everything that needs to survive a reinstalled laptop, be shared across a team, or require centralized LLM access control and cost tracking.

## 5. Data Model

Postgres replaces the prototype's filesystem-as-database entirely. Large binary artifacts (video, HAR files, traces, screenshots, audio) live in Blob Storage; Postgres holds metadata and references only. All tables carry `org_id` even in the single-org Phase 0/1 deployment, so multi-tenancy in Phase 3 is a WHERE-clause concern, not a schema migration.

| Table | Purpose | Key columns |
|---|---|---|
| `organizations` | Tenant boundary | `id`, `name`, `plan`, `settings jsonb` |
| `users` | Accounts | `id`, `org_id`, `email`, `password_hash`, `role` |
| `portal_profiles` | Auto-inferred, per-domain automation profile — **replaces hand-written `configs/*.json`** | `id`, `org_id`, `domain`, `display_name`, `inferred_login_url`, `inferred_success_signal`, `metadata jsonb`, `first_recorded_at`, `last_recorded_at` |
| `recordings` | One capture session | `id`, `portal_profile_id`, `user_id`, `status`, `capture_blob_prefix`, `has_audio bool`, `created_at` |
| `reference_trajectories` | Deterministic extraction output for a recording | `id`, `recording_id`, `trajectory_blob_ref`, `created_at` |
| `golden_scripts` | **Versioned** generated script | `id`, `portal_profile_id`, `version`, `parent_version_id`, `script_blob_ref`, `input_schema jsonb`, `output_schema jsonb`, `status` (`draft` / `certified` / `active` / `deprecated`), `created_from` (`recording_id` or `heal_event_id`), `created_at` |
| `heal_events` | A self-heal attempt, automatic or on-demand | `id`, `golden_script_id`, `trigger` (`runtime_auto` / `on_demand`), `failure_evidence_blob_ref`, `proposed_version_id`, `diff_summary jsonb`, `status` (`pending_approval` / `approved` / `rejected` / `auto_approved`), `created_at`, `resolved_at`, `resolved_by` |
| `action_cache` | Resolved-selector cache per step, for fast replay and healing (see §7.1) | `id`, `golden_script_id`, `step_index`, `cache_key` (hash of instruction + URL pattern + DOM/AX fingerprint), `selector_candidates jsonb` (ranked list), `method`, `last_verified_at`, `miss_count` |
| `runs` | An execution of a golden script | `id`, `golden_script_id`, `execution_location` (`desktop_app` / `cloud` / `external`), `status`, `started_at`, `finished_at`, `result_blob_ref`, `cost_usage jsonb` |
| `video_assets` | Recorder/editor output (Phase 2) | `id`, `recording_id`, `kind` (`raw` / `narrated_edit`), `storage_ref`, `transcript_ref`, `duration_seconds`, `status` |
| `video_steps` | Structured step list backing the video/document view (Phase 2, schema defined now — see §8.1) | `id`, `video_asset_id`, `order_index`, `screenshot_ref`, `dom_action_event jsonb`, `caption_text`, `narration_audio_ref`, `narration_transcript`, `duration_ms` |
| `usage_ledger` | Per-org/user LLM cost tracking — **replaces the prototype's global in-memory counter** | `id`, `org_id`, `user_id`, `run_id`, `provider`, `model`, `tokens_in`, `tokens_out`, `cost_usd`, `created_at` |
| `audit_log` | Present from day one; enforcement is a later hardening pass (§13) | `id`, `org_id`, `user_id`, `action`, `resource_type`, `resource_id`, `phi_flag bool`, `metadata jsonb`, `created_at` |
| `secrets_refs` | Pointers into Key Vault — **no raw secret material in Postgres, ever** | `id`, `org_id`, `portal_profile_id`, `secret_provider`, `secret_key_ref` |

## 6. Core Engine Architecture (Python)

One Python package, `atlas_engine`, used identically by the desktop app today and any future cloud-hosted runner. It replaces the prototype's `core/src/{capture,trajectory,brain,generate,verify}` (TypeScript, generating Python) with native Python modules of the same names and responsibilities — the *algorithms* are carried forward deliberately (see §16); only the implementation language and the storage/config layers change.

### 6.1 Capture
Launches a real, headed Chromium session via Playwright-Python. Records DOM actions and network activity using Playwright's **public** event and instrumentation APIs (`page.on("framenavigated")`, `page.on("request"/"response")`, an injected recorder overlay script we own) — replacing the prototype's use of Playwright's private `enableRecorder` IPC channel, which is undocumented and can break on any Playwright upgrade. Captures, per step: before/after DOM snapshots, screenshots, timing, network deltas, and — new — an optional synchronized microphone track (see §8) for the future video knowledge base. A floating "extract this page" affordance (carried over from the prototype) lets the user mark structured-data outputs during recording.

### 6.2 Trajectory Extraction
Deterministic, no LLM — a straight port of the prototype's `core/src/trajectory/extract.ts`. Converts raw capture evidence into a `ReferenceTrajectory`: an ordered list of steps, each with best-first locator candidates (role → testid → id → text → XPath, the same durability ranking the prototype already uses), expected effects (DOM changed / URL changed / popup opened / download started / network delta), and wait ceilings derived from observed timing.

### 6.3 Portal Profile Auto-Inference
New component, replacing manual `configs/<portal>.json` authoring entirely. On the first recording of a previously-unseen domain, this module creates a `portal_profiles` row by inspecting the capture: the URL the session started at, the URL where a password field first appeared (→ inferred login URL), and a DOM-state fingerprint captured immediately after the first successful post-login navigation (→ inferred success signal). On subsequent recordings of the same domain, the profile is refined (not overwritten) — e.g., a second successful login tightens confidence in the login-detection heuristic. No LLM call is required for the common case; an LLM-assisted fallback proposes login/success-signal candidates only when the heuristics are ambiguous (e.g., a single-page app with no navigation event), and the user's next recorded login confirms or corrects it.

### 6.4 The Brain
The step executor and repair loop, ported natively into Python (no more stdio JSON-lines protocol between a TypeScript controller and a generated Python subprocess — it is one process, one language). Per step: **perceive** (read live DOM/screenshot) → **act** (execute the recorded gesture exactly) → **evaluate** (deterministic comparison of observed effects against the reference trajectory) → **think only if off-track** (a bounded, multimodal LLM call, given the live screenshot, the reference screenshot, the sanitized live DOM, and prior attempts in this step, returning one action from a closed vocabulary — `replace_selector`, `set_gesture`, `force_click`, `wait_longer`, `insert_step`, `remove_step`, `dismiss`, `click_coordinates`, `declare_blocker`, `give_up` — validated against the live DOM before being applied). This is the prototype's best idea, carried forward unchanged in substance.

### 6.5 Golden Script Emission
Deterministic, no LLM. Converts a verified trajectory into a self-contained `playwright.sync_api` Python script with argparse CLI, per-step failure diagnostics, and (for `extract`-kind outputs) inlined structured-data extraction helpers — the same output shape the prototype already produces, generated by one Python module instead of duplicated string-template emitters.

### 6.6 Certification
Re-runs the frozen golden script K times from a fresh session (default 2) plus one run with varied parameter values, to catch flakiness before a script is marked `certified`. A failure during certification is routed back through the brain for a bounded repair attempt before certification is retried.

### 6.7 What is retired
The prototype's second, parallel "classic" pipeline (codegen-import + reactive crash-and-restart healing) and its independent HAR-triage/extract implementation are **not** ported. The Brain path already subsumes what they were trying to do, and maintaining two extraction implementations feeding one code generator was an unforced duplication in the original design.

## 7. Self-Healing & Versioning Architecture

### 7.1 Action Cache
Every step of a golden script, once resolved, is cached as a row in `action_cache`: a ranked list of selector candidates (not a single selector — carrying the durability ranking from §6.2 forward so a fallback candidate is tried before falling back to the LLM), keyed by a hash of `{instruction, normalized URL pattern, DOM/AX-tree fingerprint}` — deliberately excluding parameter *values* so that a script run with different input data still hits the same cache entry. This mirrors the concrete design used by Stagehand (a comparable AI-browser-automation project — see §17): their cached `Action` object is `{selector, description, method, arguments}`, keyed server-side by a hash of method + normalized URL + DOM/AX fingerprint + instruction, with the explicit invariant *"a wrong cached action is worse than a slow one."* Atlas adopts the same invariant: any failure during replay against a cached candidate (element not found, exception, or a failed post-condition check) is treated as a **soft miss**, never a hard error — the engine falls through to the next-ranked candidate, and if all candidates miss, to the brain's live repair loop, which writes a fresh cache entry keyed by the new DOM fingerprint. A stale entry is never fuzzy-matched or force-applied.

### 7.2 Repair Persistence
When the brain successfully repairs a step, that repair is not a silent in-place mutation of the cache row. It is recorded as a `heal_events` row carrying the failure evidence, a diff of the old vs. new step (selector/gesture/wait), and a `status`. This follows the pattern described by Pace for continual-learning computer-use agents (see §17): a successful repair becomes a durable, named, auditable artifact — closer to "a committed patch" than "an updated cache entry" — so that future runs inherit the fix deliberately, not implicitly, and a human or later audit can see exactly what changed and why. Atlas defines an explicit promotion rule that Pace's own public writeup leaves unspecified: a repair is only eligible for promotion to `approved`/`active` after (a) the same repair succeeds against a fresh live check, and (b) a human approves it in the dashboard (default) or it is auto-approved because certification passed cleanly (configurable per org, off by default — see §7.3).

### 7.3 Approval Workflows
Two triggers into one repair service:
- **Automatic, for scripts run via the desktop app or a future Atlas-hosted runner.** A failure during a run triggers the brain's repair loop scoped to the failing step. On success, a new `golden_scripts` version is created with `status = draft` and a linked `heal_events` row with `status = pending_approval`. The previously-active version keeps running in production until a human approves the new one from the web dashboard, which shows the diff and the failure/repair evidence side by side.
- **On-demand, for scripts running anywhere else.** Atlas cannot observe a failure it doesn't run. The dashboard exposes a "Self-Heal" action on any golden script: the user reports (or the desktop app re-validates live against the portal) that it's broken, the same repair loop runs, and the result is a new proposed version awaiting the same approval step. The user then takes the approved script wherever they run it.

## 8. Video & Voice Knowledge Base (Phase 2 design; data captured starting Phase 0)

The user's long-term goal is a Guidde-style narrated video knowledge base built from the same recordings used to author golden scripts, so one capture session serves two purposes. Building the full editor/narration UI is Phase 2 (§18), but the capture layer is designed for it starting now, so no second recording mechanism is ever needed.

### 8.1 Step Schema
A step is a first-class structured object, not a video timecode — modeled on the document/video split visible in Guidde's product behavior (editing the step list detaches it from the rendered video; reordering operates on the step list, not raw footage — see §17): `video_steps(order_index, screenshot_ref, dom_action_event, caption_text, narration_audio_ref, narration_transcript, duration_ms)`. The **step list is the canonical source of truth**; a rendered video is a projection of it, regenerated per-step (not as one monolithic re-encode) when a step's caption or narration changes.

### 8.2 Capture vs. Build
Captured starting Phase 0 (cheap to add now, expensive to retrofit): an optional synchronized microphone track during recording, and the same step-level screenshots/timing the golden-script pipeline already needs for the brain's evidence trail — reused, not duplicated, for the eventual video guide. Built starting Phase 2: AI-drafted per-step captions, per-step text-to-speech narration, the structured step editor (edit/reorder/split steps, not scrub raw video), and a searchable, shareable knowledge-base view in the web dashboard.

## 9. Desktop EXE Architecture

A local FastAPI service (the `atlas_engine` package plus a thin local API layer) is packaged with PyInstaller into a native Windows executable (macOS to follow). The UI is the same React application used by the web platform, shown in a `pywebview` native window that talks to the local FastAPI service on `localhost` — one frontend codebase, two shells, rather than a second UI implementation. Local state (recordings in progress, the action cache, offline run history) is kept in SQLite and synced to the cloud Postgres/Blob store opportunistically in the background; the app remains fully functional recording-to-certified-golden-script while offline, syncing when connectivity returns. Chromium is bundled or installed on first run via Playwright's standard install mechanism.

## 10. Cloud Platform Architecture

Stays on Azure, matching the org's existing Azure OpenAI/Blob/Key Vault footprint: a containerized, multi-tenant FastAPI application (Postgres Flexible Server, Blob Storage, Key Vault, Azure OpenAI), deployed via infrastructure-as-code from day one so dev/staging/prod are reproducible. It serves the web dashboard (team/org management, run history, golden-script library with version diffs, heal-approval queue, and — starting Phase 2 — the video knowledge base), brokers all LLM calls made by every desktop app under an org's license (so cost and usage are centrally tracked in `usage_ledger` rather than the prototype's per-process in-memory counter), and is the durable store every desktop install syncs to.

## 11. LLM Integration Layer

Azure OpenAI remains the default and only provider at launch, matching current infrastructure and existing PHI-handling posture, but the client interface (already a factory pattern in the prototype) is kept provider-agnostic so a second provider (e.g., Anthropic, for cases where multimodal reasoning quality matters more than Azure-tenant data residency) can be added later without touching call sites. Every call remains temperature 0, JSON-mode where structured output is expected, and preceded by the same DOM/HAR/secret sanitization discipline the prototype already has (ported forward, not redesigned) — screenshots and DOM sent to the model never contain raw PHI values, only structure.

## 12. Frontend Architecture Direction

The user has asked for a genuinely enterprise-grade UI, which this document intentionally does not attempt to fully specify — that is a dedicated design pass to run after this architecture is approved. What this document does fix structurally, because it affects the data/API layer: real client-side routing (the prototype has none — tab state isn't even in the URL), a shared design-system/component library used by both the web dashboard and the EXE's embedded shell, and a data layer built around the versioned/queryable Postgres model in §5 rather than a single global SSE event bus assuming one run is ever active system-wide (a real limitation in the prototype's frontend today).

## 13. Security, Compliance & PHI Handling

Carried forward at "best effort now, formalized later" per explicit direction: DOM/HAR sanitization before any LLM call, secret scrubbing in recordings, no LLM calls inside shipped golden scripts, and PHI-bearing tables/columns already flagged (`audit_log.phi_flag`, `recordings`/`video_assets` treated as PHI-bearing by convention) even though enforcement (mandatory audit logging on every access, encryption-at-rest verification, formal retention/purge policies, additional BAAs) is deferred to a later hardening phase (§18, Phase 3). This sequencing is a deliberate scope decision, not an oversight — the schema is shaped so that hardening pass does not require restructuring the data model.

## 14. Testing Strategy

The prototype has zero automated tests anywhere in the codebase — a genuine gap this rebuild closes from the start: `pytest` unit tests for the engine (trajectory extraction against fixture HAR/DOM captures, the brain's `evaluate()` heuristics, sanitizers, locator-candidate resolution), integration tests running the full capture→brain→emit→certify pipeline headlessly against seeded local fixture portals in CI, and contract tests for the local FastAPI API the frontend depends on. Frontend component tests are added as the new UI is built, rather than retrofitted later.

## 15. Deployment & Packaging

**Cloud platform:** Docker container, deployed to Azure (Container Apps or App Service), Postgres Flexible Server, Blob Storage, Key Vault, Azure OpenAI, provisioned via IaC (Bicep or Terraform), with CI/CD via GitHub Actions.

**Desktop app:** PyInstaller build of the local FastAPI + `atlas_engine` service, `pywebview` shell embedding the shared React build, packaged as a Windows installer first. An auto-update mechanism checks for and installs new versions without requiring a manual reinstall.

**Proposed repo layout** (this repo, `/Users/apple/AtlasAI`, currently empty):
```
atlas-ai/
├── engine/       # atlas_engine — the Python package: capture, trajectory, brain, emit, certify
├── backend/      # cloud multi-tenant FastAPI app; imports engine only for on-demand self-heal
├── desktop/      # PyInstaller spec, pywebview shell entrypoint, local API layer
├── frontend/     # shared React app, built for both the web dashboard and the desktop shell
├── infra/        # Bicep/Terraform for the Azure platform
└── docs/
```

## 16. Migration from `/Users/apple/Desktop/atlasAI`

The old repository is treated as **reference material for its algorithms, not a codebase to incrementally migrate** — the runtime language itself is changing (TypeScript orchestration → native Python), and the two-pipeline duplication needs eliminating rather than carrying forward, so an in-place migration would fight the new architecture at every step.

**Ported deliberately (the valuable IP):** the Brain's perceive/act/evaluate/think loop and its closed repair-action vocabulary; the locator durability ranking (testid → role+name → id → text → XPath); the deterministic trajectory-extraction approach (no LLM); the DOM/HAR sanitizer designs; the certification-by-repeated-run concept; the "recording is ground truth, never distilled" principle itself.

**Rebuilt, not ported:** the TypeScript orchestration layer and all four generated-Python-string emitters (collapsed into one native Python engine); the filesystem-as-database storage pattern (replaced by §5); the manual JSON config system (replaced by §6.3); the single-global-run-lock concurrency model; the two overlapping pipelines (only the Brain path survives); the frontend (rebuilt as an enterprise-grade app per §12, informed by but not copy-pasted from the existing React dashboard); the private Playwright API usage in the recorder (replaced with public APIs per §6.1).

**Not retained at all:** the "classic" reactive heal-and-restart pipeline, the codegen-import LLM stage, the legacy static HTML dashboard shipped as a fallback, and the org-specific hardcoded values found in generated output (e.g. hardcoded Microsoft Graph client IDs and sender domains baked into a specific customer's golden script) — these become per-org configuration, not source-code constants.

## 17. Reference Platforms Informing Future Architecture

Four platforms were researched in depth because they represent close analogs to where this product is headed, and specific mechanisms from each are cited by section above rather than only summarized here:

- **Stagehand** (an open-source AI browser-automation SDK on top of Playwright) validates and gives concrete shape to the action-cache design in §7.1: cache a resolved action, not just a selector; key the cache on instruction + URL pattern + DOM/AX-tree fingerprint, excluding parameter values; treat any cache-hit failure as a soft miss that falls through to live re-resolution, never a hard error; the explicit invariant "a wrong cached action is worse than a slow one" is adopted verbatim as a design rule for Atlas's own action cache.
- **Guidde** (an AI-generated video-guide product) validates the step-schema design in §8.1: a recording should be represented as a structured, ordered list of steps (screenshot + caption + independent narration), with video as a rendered projection of that list rather than the source of truth — confirmed by Guidde's own behavior of "detaching" the video from the document the moment a user edits the step list directly, and by reordering being a step-list operation rather than a video-timeline operation.
- **Pace** ("continual learning in computer use") validates the repair-persistence design in §7.2: a successful self-repair should become a durable, named, auditable patch that all future runs inherit deliberately — not a silently-overwritten cache row. Pace's own public writeup does not specify a threshold for when an agent should self-repair versus escalate to a human; Atlas defines its own explicit rule (§7.2/§7.3) rather than assume one exists to copy.
- **Health API Guy's "An Abridge Acquihire"** (and related posts on the same substack about EHR integration architecture) supplies a four-mode taxonomy for portal automation — network interception, headless browser (Atlas's current mode), browser-extension-based (riding inside a user's live logged-in session, inheriting auth without ever storing a password), and computer-use/vision (reserved for thick-client/Citrix EHRs with no DOM at all). This directly informs the priority order in the roadmap below: browser-extension capture is the recommended *next* capture mode after the desktop app, ahead of computer-use, both because it reuses the same "record what I do" product surface and because it has a more favorable PHI/detectability profile for healthcare portals than a scripted headless bot. The same source also documents at least one EHR vendor (PointClickCare) banning unsanctioned browser-extension access to their platform — a live governance/ToS risk that any future extension-mode design needs to address, not just a technical build item.

## 18. Roadmap & Phasing

- **Phase 0 — Foundation.** Repo scaffold (§15), Postgres schema (§5), basic auth, desktop app shell, `atlas_engine` ported natively to Python with the Brain as its core, single unified pipeline, zero-config portal profile inference. *Exit criterion: a user installs the desktop app, records themselves using any portal, and receives a certified golden Playwright script — functional parity with the prototype's best path, on solid ground.*
- **Phase 1 — Healing, versioning, and the enterprise dashboard.** Versioned golden scripts with diffs, the automatic and on-demand self-heal workflows and their approval gates (§7), the action cache (§7.1), persisted per-org usage tracking, and the first pass of the enterprise-grade web dashboard (§12).
- **Phase 2 — Video & voice knowledge base.** Microphone capture wiring already present from Phase 0 is put to use: AI-drafted per-step captions, per-step narration/TTS, the structured step editor, and a searchable knowledge-base view in the dashboard (§8).
- **Phase 3 — Market scale and hardening.** Multi-tenant billing and org plans, optional Atlas-hosted cloud execution workers for users who want unattended cloud runs, browser-extension capture mode (§17), deeper continual-learning self-healing (compounding, auditable repairs per §7.2 maturing toward less LLM involvement over time), and full HIPAA-grade compliance hardening (§13).

## 19. Open Questions / Risks

- **Portal-profile auto-inference reliability on non-standard login flows** (SPA logins with no navigation event, multi-factor gates, SSO redirects through a third-party IdP) is a heuristic, not a certainty — Phase 0 should budget time to validate it against a handful of real, varied portals (not just the EHR portals the prototype already handles) before treating zero-config as fully proven.
- **The desktop app's offline-then-sync model** (§9) needs a concrete conflict-resolution rule for the rare case where the same golden script is edited/healed from two of a user's machines before either syncs — not specified in this document, needs a decision during Phase 1 implementation planning.
- **Pace's public writeup provides no self-repair-vs-escalate threshold** to benchmark against (§17); Atlas's own rule (§7.2) is a starting hypothesis, not something copied from prior art, and should be revisited once real heal-event data exists.
- **Browser-extension governance risk** (§17): at least one major EHR vendor has banned unsanctioned extension-based access to their platform. Phase 3 extension-mode design needs to account for this before committing engineering time to it.

## 20. References

- Prototype codebase and documentation: `/Users/apple/Desktop/atlasAI` (`docs/ARCHITECTURE.md`, `docs/FINAL-FLOW.md`, `docs/EVOLUTION.md`, `docs/RUNTIME-SELF-HEAL-PLAN.md`, `docs/DATA-EXTRACTION-*.md`, and the Stage 0–8 architecture documents).
- Stagehand: https://www.stagehand.dev/ and https://github.com/browserbasehq/stagehand
- Guidde: https://www.guidde.com/
- Pace, "Continual Learning in Computer Use": https://withpace.com/news/continual-learning-in-computer-use
- Health API Guy, "An Abridge Acquihire": https://healthapiguy.substack.com/p/an-abridge-acquihire
