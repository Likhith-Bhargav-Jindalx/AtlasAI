# Backend Production Hardening & Azure Deployment — Design

**Status:** Draft — pending user sign-off before implementation planning
**Date:** 2026-08-17
**Author:** Likhith Bhargav (Anka Health), designed with Claude
**Parent context:** First of two sub-projects toward "make Atlas AI production grade and shippable as a Windows exe." This sub-project covers the shared backend; the desktop exe packaging is a separate, subsequent design.
**Builds on:** `docs/superpowers/specs/2026-08-12-atlas-ai-production-architecture-design.md` (§10 Cloud Platform Architecture, §15 Deployment & Packaging), `engine/README.md`

---

## 1. Context & Problem Statement

Atlas AI's rollout plan is: tens of operations agents each run a desktop exe, log in with their own credentials, enter a target portal URL, and record themselves performing a workflow. Those recordings need to land somewhere all agents and a developer can share — currently there is no such place. The `engine/` package already implements a working FastAPI app (auth, recording requests, live recording start/stop, heal events) with a real Postgres-backed data model and 71 passing tests, but it has only ever run against a local dev Postgres on a single machine, with no deployment target, thin logging, no CI, and secrets that default to insecure placeholders.

This sub-project makes that existing backend reachable, reliable, and observable for a real multi-agent rollout — without building the separate multi-tenant `backend/` app the original architecture spec sketched for an eventual SaaS product. That split is explicitly deferred: this is one internal org today, not multiple tenants, and building org-isolation/licensing scaffolding now would be speculative work against a requirement that doesn't exist yet.

## 2. Goals & Non-Goals

**Goals**
- Deploy the existing `engine/` FastAPI app to Azure as the shared backend every agent exe and the developer console talk to.
- Replace the local dev Postgres with a managed, durable Azure Postgres instance; require Azure Blob Storage in production (no silent local-disk fallback).
- Close the "no CI" gap: tests run and gate every PR; deploys are automated, not manual.
- Add enough structured logging and platform-level metrics to answer "what is happening across dozens of concurrent agent sessions right now" without adding a new APM integration.
- Harden the two concrete reliability gaps that matter most for concurrent agent use: unhandled-exception leakage, and recording-submission durability under a network blip.
- Keep secrets out of git and out of the container image, using Azure Container Apps' built-in secrets — without building a Key Vault integration this phase.

**Non-goals**
- The separate multi-tenant `backend/` app, org-isolation enforcement, and licensing/LLM-cost brokering from the original architecture spec's §10 — deferred to a later phase, if/when this becomes a multi-org product.
- Key Vault, formal audit-log enforcement, encryption-at-rest verification, retention/purge policies — the schema already supports adding these later (per the original spec's §13); not built now, by explicit user direction.
- A staging environment — one production environment for this internal tool; Container Apps' revision-based rollout (§7) provides a rollback path without needing a parallel environment.
- Application Insights / OpenTelemetry SDK integration — Container Apps' built-in stdout/stderr → Log Analytics pipeline is sufficient for this phase's observability goal.
- Any change to the recording/authoring/brain pipeline's core logic — this sub-project is deployment, config, logging, and error-handling around the existing engine, not a rebuild of it.

## 3. Architecture

```
GitHub Actions ──build/push──► Azure Container Registry
       │                              │
       │ (on merge to main)           │ image pull
       ▼                              ▼
  run pytest/vitest           Azure Container Apps (atlas-engine-api)
  on every PR                        │
                                      │ ATLAS_DATABASE_URL, session secret,
                                      │ storage conn string, Azure OpenAI key
                                      │ — all Container Apps secrets
                                      ▼
                    ┌─────────────────┴─────────────────┐
                    ▼                                   ▼
     Azure Database for PostgreSQL           Azure Blob Storage
     (Flexible Server)                       (recordings, videos,
     — replaces dev docker-compose             golden scripts, traces)
       Postgres 1:1, same
       postgresql+psycopg URL shape

     Log Analytics workspace ◄── stdout/stderr + Container Apps
     (structured JSON logs,        platform metrics (request count/
      queryable by request-id)     latency/error rate)
```

Every agent exe and the developer console (browser or exe) point at the Container App's HTTPS ingress URL instead of `localhost`. No change to the API's routes or request/response shapes — this is purely making the existing surface reachable and durable.

## 4. Components

- **Azure Container Registry (ACR)** — one repository, `atlas-engine-api`, tagged by git SHA.
- **Azure Container Apps** — one app, `atlas-engine-api`, HTTPS ingress, min replicas ≥ 1 (avoid cold-start on the first agent of the day), scale-out on concurrent request count. Deploys are new **revisions**; traffic shifts to a new revision only after it passes a startup health check, and a bad deploy is rolled back by shifting traffic back to the prior revision.
- **Azure Database for PostgreSQL – Flexible Server** — sized for tens of concurrent light-write connections (agents submitting recordings, not high-frequency transactional load). Connection string plugs directly into the existing `ATLAS_DATABASE_URL` setting; no code change.
- **Azure Blob Storage** — one storage account/container, matching `ATLAS_AZURE_STORAGE_CONTAINER`. `ATLAS_REQUIRE_AZURE_STORAGE=true` in production so a misconfiguration fails loudly at startup instead of silently writing to the container's ephemeral local disk.
- **Log Analytics workspace** — provisioned alongside the Container App; receives stdout/stderr and platform metrics automatically.
- **GitHub Actions** — two workflows (§7).

## 5. Config & Secrets

Container Apps' native **secrets** (encrypted, referenced by env var name, never baked into the image or committed to git) hold: `ATLAS_SESSION_SECRET` (a real random value, replacing the dev placeholder), `ATLAS_DATABASE_URL`, `ATLAS_AZURE_STORAGE_CONNECTION_STRING`, and the `ATLAS_AZURE_OPENAI_*` credentials. `ATLAS_REQUIRE_AZURE_STORAGE` and non-secret config (container name, log level) are plain Container Apps env vars. No Key Vault integration this phase — this is baseline secret hygiene using what Container Apps already provides, not a security hardening project.

## 6. Logging & Observability

- Switch the app's logging to structured JSON (Python `logging` with a JSON formatter), emitted to stdout so Container Apps' existing log pipeline picks it up with no extra plumbing.
- Attach a request-id (generated per inbound request, or passed through from the exe if it already has a correlation id) to every log line for that request — auth, recording start/stop, storage upload, heal — so one agent's session can be traced end-to-end in Log Analytics.
- Rely on Container Apps' built-in platform metrics (request count, latency, error rate, replica count) for the operational "is it healthy right now" view — no custom metrics pipeline this phase.

## 7. Error Handling Hardening

- **Global exception handler** on the FastAPI app: any unhandled exception is logged server-side with full traceback and request-id, and returns a clean, consistent JSON error shape to the client — no stack traces or internal details leak to the exe.
- **Recording submission durability**: the step where a finished recording's artifacts (video, trajectory JSON, screenshots) upload to Blob Storage gets retry-with-backoff (a transient network blip during upload shouldn't lose an agent's completed session). If upload still fails after retries, the recording is left in an explicit failed/incomplete status (visible to the developer console) rather than silently vanishing or being marked as if it succeeded.

## 8. CI/CD

Two GitHub Actions workflows:

- **`ci.yml`** (on every PR): run `pytest -v` (engine, against a fresh Postgres service container) and the frontend's `vitest` suite. Merge is blocked on failure.
- **`deploy.yml`** (on merge to `main`): build the Docker image, push to ACR tagged with the commit SHA, run `alembic upgrade head` against the production Flexible Server, then deploy the new image as a Container Apps revision and shift traffic once its health check passes.

A `Dockerfile` for `engine/` (currently absent) is new work under this sub-project — multi-stage build: install dependencies + Playwright's Chromium, copy the app, run via `uvicorn`.

## 9. Testing

No new testing strategy — the existing 71-file pytest suite already covers the engine's behavior and runs against fixtures/fake LLM clients (per `engine/README.md`), so it ports into CI unchanged. Net-new tests scoped to this sub-project's new behavior only:
- The global exception handler returns the clean error shape and logs the traceback.
- The recording-upload retry logic recovers from a transient failure and correctly marks a recording failed after exhausting retries.

## 10. Rollout

1. Provision Azure resources (ACR, Container Apps environment + app, Flexible Server, storage account, Log Analytics workspace).
2. Add the `Dockerfile`, structured logging, global exception handler, and upload retry logic to `engine/`.
3. Stand up the two GitHub Actions workflows; get `ci.yml` green on a PR before touching `deploy.yml`.
4. Run `alembic upgrade head` against the new Flexible Server once, manually, to establish the schema; subsequent migrations run through `deploy.yml`.
5. First deploy via `deploy.yml`; smoke-test auth + a recording start/stop against the deployed URL before pointing any real agent at it.

## 11. Open Questions / Risks

- **Flexible Server sizing** isn't pinned down yet — start on the smallest burstable tier and scale up if tens of concurrent agents' write load demands it; this is a cheap-to-fix operational tuning question, not a design blocker.
- **Container Apps min-replica cost tradeoff**: keeping ≥1 replica warm avoids cold-start latency for the first agent each day but incurs always-on cost; revisit if cost becomes a concern.
- **Org bootstrapping in production**: today, creating an organization/user is a manual CLI/Python snippet (per `engine/README.md`). That's acceptable for tens of agents at rollout, but is a candidate for a small admin script or endpoint if onboarding agents becomes frequent — not designed here.
