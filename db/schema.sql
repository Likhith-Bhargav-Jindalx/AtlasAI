-- Atlas AI — PostgreSQL schema
-- Companion to docs/superpowers/specs/2026-08-12-atlas-ai-database-design.md
--
-- Design rules encoded here:
--   1. Every table carries org_id, even in the single-org Phase 0/1 deployment,
--      so Phase 3 multi-tenancy is a WHERE-clause concern, not a migration.
--   2. Large binary artifacts (video, HAR, traces, screenshots, scripts) are
--      NEVER stored in Postgres — only a *_blob_ref / *_ref pointer into Blob
--      Storage. Postgres holds metadata and relationships only.
--   3. Raw secret material is never stored here — secrets_refs holds a
--      pointer into Key Vault, resolved at runtime.
--   4. golden_scripts and heal_events reference each other (a golden_scripts
--      row can be created FROM a heal_event, and a heal_event PROPOSES a
--      golden_scripts row). Both FKs are added after both tables exist, and
--      both are nullable, to avoid a hard circular dependency at creation time.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Identity & Organization ────────────────────────────────────────────────

CREATE TYPE org_plan AS ENUM ('internal', 'team', 'enterprise');

CREATE TABLE organizations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    plan        org_plan NOT NULL DEFAULT 'internal',
    settings    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TYPE user_role AS ENUM ('owner', 'admin', 'member');

CREATE TABLE users (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email          TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    role           user_role NOT NULL DEFAULT 'member',
    display_name   TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, email)
);

-- ── Portal & Recording ──────────────────────────────────────────────────────

CREATE TABLE portal_profiles (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    created_by               UUID REFERENCES users(id) ON DELETE SET NULL,
    domain                   TEXT NOT NULL,
    display_name             TEXT,
    inferred_login_url       TEXT,
    inferred_success_signal  JSONB,
    confidence_score         NUMERIC(3, 2),
    metadata                 JSONB NOT NULL DEFAULT '{}',
    first_recorded_at        TIMESTAMPTZ,
    last_recorded_at         TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, domain)
);

CREATE TYPE secret_provider AS ENUM ('env', 'azure_keyvault');

CREATE TABLE secrets_refs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    portal_profile_id   UUID REFERENCES portal_profiles(id) ON DELETE CASCADE,
    label               TEXT NOT NULL,               -- e.g. "password", "otp_client_id"
    secret_provider     secret_provider NOT NULL DEFAULT 'env',
    secret_key_ref      TEXT NOT NULL,                -- key name / vault URI, never the value
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (portal_profile_id, label)
);

CREATE TYPE recording_status AS ENUM ('capturing', 'captured', 'failed', 'discarded');

CREATE TABLE recordings (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    portal_profile_id     UUID NOT NULL REFERENCES portal_profiles(id) ON DELETE CASCADE,
    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status                recording_status NOT NULL DEFAULT 'capturing',
    capture_blob_prefix   TEXT NOT NULL,               -- e.g. "orgs/<org>/recordings/<id>/"
    has_audio             BOOLEAN NOT NULL DEFAULT false,
    duration_seconds      INTEGER,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at          TIMESTAMPTZ
);

CREATE INDEX idx_recordings_portal_profile ON recordings (portal_profile_id, created_at DESC);

CREATE TABLE reference_trajectories (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id          UUID NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    trajectory_blob_ref   TEXT NOT NULL,
    step_count            INTEGER,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_reference_trajectories_recording ON reference_trajectories (recording_id);

-- ── Golden Scripts & Self-Healing ───────────────────────────────────────────

CREATE TYPE golden_script_status AS ENUM ('draft', 'certified', 'active', 'deprecated');

CREATE TABLE golden_scripts (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    portal_profile_id           UUID NOT NULL REFERENCES portal_profiles(id) ON DELETE CASCADE,
    version                     INTEGER NOT NULL,
    parent_version_id           UUID REFERENCES golden_scripts(id) ON DELETE SET NULL,
    script_blob_ref             TEXT NOT NULL,
    input_schema                JSONB NOT NULL DEFAULT '{}',
    output_schema               JSONB NOT NULL DEFAULT '{}',
    status                      golden_script_status NOT NULL DEFAULT 'draft',
    created_from_recording_id   UUID REFERENCES recordings(id) ON DELETE SET NULL,
    created_from_heal_event_id  UUID,  -- FK added below, after heal_events exists (circular reference)
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (portal_profile_id, version)
);

CREATE INDEX idx_golden_scripts_active ON golden_scripts (portal_profile_id, status)
    WHERE status = 'active';

CREATE TYPE heal_trigger AS ENUM ('runtime_auto', 'on_demand');
CREATE TYPE heal_status AS ENUM ('pending_approval', 'approved', 'rejected', 'auto_approved');

CREATE TABLE heal_events (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    golden_script_id            UUID NOT NULL REFERENCES golden_scripts(id) ON DELETE CASCADE,
    proposed_version_id         UUID REFERENCES golden_scripts(id) ON DELETE SET NULL,
    trigger                     heal_trigger NOT NULL,
    failure_evidence_blob_ref   TEXT,
    diff_summary                JSONB,
    status                      heal_status NOT NULL DEFAULT 'pending_approval',
    resolved_by                 UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at                 TIMESTAMPTZ
);

CREATE INDEX idx_heal_events_pending ON heal_events (org_id, status)
    WHERE status = 'pending_approval';

-- Close the circular reference now that heal_events exists.
ALTER TABLE golden_scripts
    ADD CONSTRAINT fk_golden_scripts_heal_event
    FOREIGN KEY (created_from_heal_event_id) REFERENCES heal_events(id) ON DELETE SET NULL;

CREATE TABLE action_cache (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    golden_script_id      UUID NOT NULL REFERENCES golden_scripts(id) ON DELETE CASCADE,
    step_index            INTEGER NOT NULL,
    cache_key             TEXT NOT NULL,          -- hash(instruction + url_pattern + dom_ax_fingerprint)
    selector_candidates   JSONB NOT NULL,          -- ranked list: [{selector, method, rank}, ...]
    method                TEXT,
    last_verified_at      TIMESTAMPTZ,
    miss_count            INTEGER NOT NULL DEFAULT 0,
    UNIQUE (golden_script_id, step_index, cache_key)
);

-- ── Execution & Cost ─────────────────────────────────────────────────────────

CREATE TYPE run_execution_location AS ENUM ('desktop_app', 'cloud', 'external');
CREATE TYPE run_status AS ENUM ('running', 'succeeded', 'failed', 'healed');

CREATE TABLE runs (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    golden_script_id      UUID NOT NULL REFERENCES golden_scripts(id) ON DELETE CASCADE,
    triggered_by          UUID REFERENCES users(id) ON DELETE SET NULL,
    execution_location    run_execution_location NOT NULL,
    status                run_status NOT NULL DEFAULT 'running',
    started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at           TIMESTAMPTZ,
    result_blob_ref       TEXT,
    cost_usage            JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_runs_golden_script ON runs (golden_script_id, started_at DESC);

CREATE TABLE usage_ledger (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id        UUID REFERENCES users(id) ON DELETE SET NULL,
    run_id         UUID REFERENCES runs(id) ON DELETE SET NULL,
    recording_id   UUID REFERENCES recordings(id) ON DELETE SET NULL,
    provider       TEXT NOT NULL,           -- e.g. "azure_openai"
    model          TEXT NOT NULL,
    tokens_in      INTEGER NOT NULL DEFAULT 0,
    tokens_out     INTEGER NOT NULL DEFAULT 0,
    cost_usd       NUMERIC(10, 4) NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_usage_ledger_org_time ON usage_ledger (org_id, created_at DESC);

-- ── Video & Voice Knowledge Base (Phase 2 — schema present from Phase 0) ────

CREATE TYPE video_asset_kind AS ENUM ('raw', 'narrated_edit');
CREATE TYPE video_asset_status AS ENUM ('processing', 'ready', 'failed');

CREATE TABLE video_assets (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    recording_id      UUID NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    kind              video_asset_kind NOT NULL DEFAULT 'raw',
    storage_ref       TEXT NOT NULL,
    transcript_ref    TEXT,
    duration_seconds  INTEGER,
    status            video_asset_status NOT NULL DEFAULT 'processing',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE video_steps (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_asset_id         UUID NOT NULL REFERENCES video_assets(id) ON DELETE CASCADE,
    order_index            INTEGER NOT NULL,
    screenshot_ref         TEXT,
    dom_action_event       JSONB,
    caption_text           TEXT,
    narration_audio_ref    TEXT,
    narration_transcript   TEXT,
    duration_ms            INTEGER,
    UNIQUE (video_asset_id, order_index)
);

-- ── Governance ───────────────────────────────────────────────────────────────
-- Present from day one; enforcement (mandatory writes on every PHI-bearing
-- access) is a Phase 3 hardening item, not built yet. See design doc §13.

CREATE TABLE audit_log (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id        UUID REFERENCES users(id) ON DELETE SET NULL,
    action         TEXT NOT NULL,
    resource_type  TEXT NOT NULL,
    resource_id    UUID,
    phi_flag       BOOLEAN NOT NULL DEFAULT false,
    metadata       JSONB NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_log_org_time ON audit_log (org_id, created_at DESC);
