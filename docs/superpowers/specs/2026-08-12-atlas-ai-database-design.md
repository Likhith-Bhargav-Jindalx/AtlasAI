# Atlas AI — Database Design

**Status:** Companion to `2026-08-12-atlas-ai-production-architecture-design.md`, §5
**Schema source of truth:** [`db/schema.sql`](../../../db/schema.sql)
**Date:** 2026-08-12

This document expands the architecture spec's data-model summary into a full schema: 14 tables across six domains, with an ER diagram, per-table field reference, and the cross-cutting design decisions that shaped it.

## Entity-relationship diagram

```mermaid
erDiagram
    ORGANIZATIONS ||--o{ USERS : employs
    ORGANIZATIONS ||--o{ PORTAL_PROFILES : owns
    ORGANIZATIONS ||--o{ SECRETS_REFS : owns
    ORGANIZATIONS ||--o{ RECORDINGS : owns
    ORGANIZATIONS ||--o{ GOLDEN_SCRIPTS : owns
    ORGANIZATIONS ||--o{ HEAL_EVENTS : owns
    ORGANIZATIONS ||--o{ RUNS : owns
    ORGANIZATIONS ||--o{ USAGE_LEDGER : owns
    ORGANIZATIONS ||--o{ VIDEO_ASSETS : owns
    ORGANIZATIONS ||--o{ AUDIT_LOG : owns

    USERS ||--o{ RECORDINGS : records
    USERS ||--o{ RUNS : triggers
    USERS |o--o{ HEAL_EVENTS : "approves (resolved_by)"
    USERS |o--o{ PORTAL_PROFILES : "first captured by"

    PORTAL_PROFILES ||--o{ RECORDINGS : "is recorded in"
    PORTAL_PROFILES ||--o{ GOLDEN_SCRIPTS : generates
    PORTAL_PROFILES |o--o{ SECRETS_REFS : requires

    RECORDINGS ||--o{ REFERENCE_TRAJECTORIES : "extracts into"
    RECORDINGS |o--o{ GOLDEN_SCRIPTS : "authors (created_from_recording_id)"
    RECORDINGS ||--o{ VIDEO_ASSETS : captures
    RECORDINGS ||--o{ USAGE_LEDGER : "incurs (authoring-time LLM calls)"

    VIDEO_ASSETS ||--o{ VIDEO_STEPS : "breaks into"

    GOLDEN_SCRIPTS |o--o{ GOLDEN_SCRIPTS : "supersedes (parent_version_id)"
    GOLDEN_SCRIPTS ||--o{ HEAL_EVENTS : "fails into"
    GOLDEN_SCRIPTS ||--o{ ACTION_CACHE : "caches steps for"
    GOLDEN_SCRIPTS ||--o{ RUNS : "executes as"

    HEAL_EVENTS |o--o| GOLDEN_SCRIPTS : "proposes (proposed_version_id)"

    RUNS ||--o{ USAGE_LEDGER : incurs

    ORGANIZATIONS {
        uuid id PK
        string name
        string slug UK
        string plan
        jsonb settings
    }
    USERS {
        uuid id PK
        uuid org_id FK
        string email
        string password_hash
        string role
    }
    PORTAL_PROFILES {
        uuid id PK
        uuid org_id FK
        uuid created_by FK
        string domain
        string inferred_login_url
        jsonb inferred_success_signal
        numeric confidence_score
    }
    SECRETS_REFS {
        uuid id PK
        uuid org_id FK
        uuid portal_profile_id FK
        string label
        string secret_provider
        string secret_key_ref
    }
    RECORDINGS {
        uuid id PK
        uuid org_id FK
        uuid portal_profile_id FK
        uuid user_id FK
        string status
        string capture_blob_prefix
        boolean has_audio
    }
    REFERENCE_TRAJECTORIES {
        uuid id PK
        uuid recording_id FK
        string trajectory_blob_ref
        int step_count
    }
    GOLDEN_SCRIPTS {
        uuid id PK
        uuid org_id FK
        uuid portal_profile_id FK
        uuid parent_version_id FK
        uuid created_from_recording_id FK
        uuid created_from_heal_event_id FK
        int version
        string script_blob_ref
        string status
    }
    HEAL_EVENTS {
        uuid id PK
        uuid org_id FK
        uuid golden_script_id FK
        uuid proposed_version_id FK
        uuid resolved_by FK
        string trigger
        string status
        jsonb diff_summary
    }
    ACTION_CACHE {
        uuid id PK
        uuid golden_script_id FK
        int step_index
        string cache_key UK
        jsonb selector_candidates
        int miss_count
    }
    RUNS {
        uuid id PK
        uuid org_id FK
        uuid golden_script_id FK
        uuid triggered_by FK
        string execution_location
        string status
        jsonb cost_usage
    }
    USAGE_LEDGER {
        uuid id PK
        uuid org_id FK
        uuid user_id FK
        uuid run_id FK
        uuid recording_id FK
        string provider
        numeric cost_usd
    }
    VIDEO_ASSETS {
        uuid id PK
        uuid org_id FK
        uuid recording_id FK
        string kind
        string storage_ref
        string status
    }
    VIDEO_STEPS {
        uuid id PK
        uuid video_asset_id FK
        int order_index
        jsonb dom_action_event
        string caption_text
        string narration_audio_ref
    }
    AUDIT_LOG {
        uuid id PK
        uuid org_id FK
        uuid user_id FK
        string action
        string resource_type
        boolean phi_flag
    }
```

A rendered, styled version of this same diagram with the full field reference is published as an artifact for easier review (see conversation).

## Design decisions

**`org_id` on every table, from Phase 0.** Even while the product is single-org internal use, every table carries `org_id` and cascades from `organizations`. This means Phase 3 multi-tenancy is a query-scoping concern, not a migration that touches every table.

**Postgres holds metadata; Blob Storage holds bytes.** No table stores a script, screenshot, video, HAR file, or trace directly — every such artifact is a `*_blob_ref` / `*_ref` string pointing into Blob Storage. This keeps the database small, fast, and cheap to back up, and matches the architecture spec's local-first design: the desktop app can write these blobs locally first and sync the reference row after.

**`golden_scripts` is append-only and self-referential, not update-in-place.** A "new version" is a new row with `parent_version_id` pointing at the one it supersedes, and `status` tracking which version is currently `active`. This is what makes the heal-and-approve workflow (architecture spec §7.3) possible: the previous `active` version keeps serving production runs, untouched, while a `draft` version is reviewed.

**The `golden_scripts` ↔ `heal_events` circular reference is intentional.** A `heal_events` row points at the `golden_scripts` row that failed (`golden_script_id`) and, once repaired, at the new version it produced (`proposed_version_id`). A `golden_scripts` row can in turn record which `heal_events` row it was created from (`created_from_heal_event_id`). Postgres handles this cleanly as two independently nullable foreign keys — `schema.sql` creates `golden_scripts` first with that column un-constrained, creates `heal_events`, then adds the constraint back via `ALTER TABLE`.

**`action_cache` is keyed to exclude parameter values.** `cache_key` is a hash of the step's instruction, a normalized URL pattern, and a DOM/accessibility-tree fingerprint — deliberately never the actual values a script was run with (a date, a patient ID). This means the same golden script run against different input data still hits the same cache entries, which is the property that makes the cache useful at all (see architecture spec §7.1 and §17 for why this mirrors Stagehand's cache-key design).

**`video_assets` / `video_steps` exist from Phase 0 even though the editor is Phase 2.** The tables are here now because the capture layer starts recording the data they need (per-step screenshots, optional narration audio) from day one — see architecture spec §8.2. An empty `video_steps` table costs nothing; retrofitting step-level capture into recordings that were never captured that way would cost a lot.

**`audit_log` exists from Phase 0 but is not yet enforced.** The table and its `phi_flag` column exist so that Phase 3 compliance hardening (architecture spec §13) is a matter of *writing to* this table everywhere it should already be written, not designing a new one under time pressure later.

## Indexing strategy

Beyond primary/foreign keys, `schema.sql` adds:
- `UNIQUE (org_id, domain)` on `portal_profiles` and `UNIQUE (portal_profile_id, version)` on `golden_scripts` — the natural business keys.
- A partial index `golden_scripts (portal_profile_id, status) WHERE status = 'active'` — the single most common lookup ("what's the live script for this portal right now") stays a fast index scan even as version history grows.
- A partial index `heal_events (org_id, status) WHERE status = 'pending_approval'` — backs the approval-queue view in the dashboard directly.
- Descending time indexes on `recordings`, `runs`, `usage_ledger`, and `audit_log` for the recency-ordered lists every dashboard view will need.
