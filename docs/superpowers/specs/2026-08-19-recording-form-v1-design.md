# Atlas Recording Form v1 — Design

**Spec source:** `atlas-recording-form-v1.md` (repo root)
**Scope guardrail:** purely additive metadata for filtering/segregation. Recording capture, golden-script emission, certify/heal/authoring pipelines are untouched.

## 1. Tabs ↔ `lob`

`lob` is a 3-value field: `AR`, `EV`, `Generic` — selected via a tab bar, not a dropdown. The Generic tab's `action` options are the union of the doc's PA + PP + Reporting-Admin action lists. Each `action` option carries an internal `source_lob` tag (`PA`/`PP`/`Reporting-Admin`) used only to decide which Section B fields apply for that action — invisible to the user.

## 2. Data model

**New org-scoped master tables** (`engine/src/atlas_engine/db/models/masterdata.py`):
- `systems(id, org_id, name, category, active)`
- `payers(id, org_id, name, active)`
- `clients(id, org_id, name, active)`
- `carc_codes(id, org_id, code, description, is_multi_use, active)`

Seeded via migration with the doc's exact values (Exa, eCW, Athena, Waystar, Stedi, Availity, Cigna, UHC, UMR, Integranet, Humana Military, Tricare For Life, Community Health Choice, Medicare IVR, NPPES, "Payer phone (no portal)"; CO-16 as the one seeded multi-use CARC code). `clients` seeded empty (org-specific, added later by whoever owns that list).

**`recording_requests`** (Section A, captured on "Start new recording"):
`lob`, `category`, `action`, `system_id` (FK, nullable), `client_scope` (JSON array of client IDs, or `["generic"]` sentinel), `reuse_scope`. Existing `title` continues to serve as `workflow_name`.

**`recordings`** (Section B, captured on submit-after-stop):
`payer_scope`, `payer_ids` (JSON array), `other_system_ids` (JSON array), `carc_code_id` (FK, nullable), `code_free_text` (nullable — clearinghouse rejections not in the CARC master), `sub_reason`, `resolution_path` (JSON array), `evidence_required` (JSON array), `report_id`, `date_param_logic`, `output_destination`, `post_processing`, `scenario_description`.

All new columns nullable — existing rows and every non-form code path (CLI `record`/`request-recording`, tests) keep working unchanged.

## 3. Backend API

- `GET /master-data/systems|payers|clients|carc-codes` — org-scoped list endpoints for populating selects.
- `POST /recording-requests` — `CreateRecordingRequestBody` gains the Section A fields (all optional at the transport level; validated below).
- `PATCH /recordings/{id}` — `UpdateRecordingDetailsBody` gains the Section B fields.
- Validation (`atlas_engine/recording_form_validation.py`, called from both endpoints):
  - `client_scope` required (min 1 entry, or the generic sentinel).
  - `payers` required unless `payer_scope == "all_payers"`.
  - `sub_reason` required when the chosen CARC code's `is_multi_use` is true.
  - `date_param_logic` rejects strings matching an absolute-date heuristic (ISO dates, "Jan 1" style, 4-digit years).
  - `post_processing` requires non-empty text or the literal `"None"`.
- Filtering: `GET /recording-requests` and `GET /recordings` gain optional `lob`, `system_id`, `client_id`, `payer_id`, `action` query params — additive, existing callers unaffected.

## 4. Frontend

One data-driven schema module (`frontend/src/lib/recordingFormSchema.ts`) defines tabs, per-tab action options (with `source_lob` tags for Generic), and Section B show-when rules as plain functions of `{lob, action, payer_scope, carc_code, resolution_path}`.

- `RecordingFormSectionA.tsx` — tab bar + Section A fields, replaces the inline title+URL form on `InboxPage`, shown as a modal.
- `RecordingFormSectionB.tsx` — conditional Section B fields, inserted into `ReviewPage` before the existing "Submit to developer" button.
- `frontend/src/lib/masterData.ts` — react-query hooks for the four master-data lists.

## 5. Out of scope (matches doc's "deferred to v2")

Fork/decision tagging during recording, exceptions/volume/AHT capture, EV benefit-field checklist, reviewer/QC sign-off, parent-child variant linking, and any master-data admin CRUD UI (seeded via migration only for v1).
