# CRUD for recording-form dropdown options (Client / Action / EHR)

## Problem

The "Start new recording" modal's Section A (`RecordingFormSectionA.tsx`)
offers three searchable dropdowns per LOB (AR/EV): Select Client, Select
Action, Select EHR. Their option lists (`CLIENT_OPTIONS`, `ACTION_OPTIONS`,
`EHR_OPTIONS`) are hardcoded `Record<Lob, string[]>` constants in
`frontend/src/lib/recordingFormSchema.ts`, with no backing database table.
Adding, renaming, or retiring an option today requires a code change and a
deploy — there is no admin surface, and no admin surface pattern exists
anywhere else in the app to copy.

A separate `masterdata.py` module (`System`, `Payer`, `Client`, `CarcCode`)
already exists for org-scoped lookup tables, but it is unrelated: its
`Client` table is a different concept from the `CLIENT_OPTIONS` dropdown
list, and it exposes read-only GET endpoints only (its own header comment
notes admin CRUD was deliberately deferred).

## Goals

- Owners/admins can add, rename, and deactivate Client/Action/EHR options
  per LOB, from the running app, with no code change or deploy.
- The recording form's existing dropdown UX (search-to-filter, per-LOB
  scoping) is unchanged from the user's perspective — only the option
  source moves from a hardcoded array to the database.
- No recorded history is broken: deactivating an option removes it from
  the dropdown for new recordings, but past recordings that used it are
  unaffected (no cascading delete, no hard delete).
- All of today's hardcoded options are preserved via a seed migration —
  the cutover adds zero and loses zero options.

## Non-goals

- `SPECIFICITY_OPTIONS` ('EHR' | 'Payor') and all of Section B's option
  lists (`RESOLUTION_PATH_OPTIONS`, `EVIDENCE_REQUIRED_OPTIONS`,
  `PAYER_SCOPE_OPTIONS`, `DENIAL_LIKE_ACTIONS`/`RESOLUTION_PATH_ACTIONS`/
  `REPORT_ACTIONS` membership sets) stay hardcoded. Out of scope per
  explicit decision.
- No change to the existing read-only `masterdata.py` tables
  (`System`/`Payer`/`Client`/`CarcCode`) or their endpoints — this is a
  new, separate table, not a retrofit of that module.
- No nested category/sub-scenario structure (e.g. "Bundled Denial →
  Corrected Claim"). Each option is a flat string within one of the three
  categories, matching today's shape exactly.
- No per-user permission UI for assigning the owner/admin role itself —
  the existing `role` column (`owner`/`admin`/`member`) is reused as-is.
- No hard delete. Deactivate only.

## Data model

One new table, not three, since the three categories are structurally
identical:

```python
# engine/src/atlas_engine/db/models/masterdata.py (new model, same file)

FORM_OPTION_CATEGORIES = ("client", "action", "ehr")

class FormOption(Base):
    __tablename__ = "form_options"

    id: Mapped[uuid.UUID]            # PK, default uuid4
    org_id: Mapped[uuid.UUID]        # FK -> organizations.id
    lob: Mapped[str]                 # Enum("AR", "EV") — reuses RECORDING_REQUEST_LOBS values used elsewhere, minus "Generic" (Section A only ever shows AR/EV tabs)
    category: Mapped[str]            # Enum(*FORM_OPTION_CATEGORIES)
    label: Mapped[str]
    active: Mapped[bool]             # default True
    created_at: Mapped[datetime]

    __table_args__ = (
        UniqueConstraint("org_id", "lob", "category", "label"),
    )
```

A migration creates this table and seeds one row per existing string
currently in `CLIENT_OPTIONS`/`ACTION_OPTIONS`/`EHR_OPTIONS` (AR and EV),
scoped to every existing `Organization` row at migration time.

## API

New endpoints in `api.py`, following the existing `require_job_function()`
pattern (called with no arguments, so only `role in ("owner", "admin")`
passes — same convention already used for other restricted mutations):

- `GET /form-options?org_id=&lob=&category=` — public to any authenticated
  user; returns `active=true` rows only. Used by the recording form.
- `GET /form-options/all?org_id=&lob=&category=` — owner/admin only;
  returns all rows including inactive. Used by the admin page.
- `POST /form-options` — owner/admin only. Body: `{org_id, lob, category,
  label}`. 409 on a duplicate `(org_id, lob, category, label)`.
- `PATCH /form-options/{id}` — owner/admin only. Body: `{label?, active?}`.
  Deactivating is `{"active": false}`; there is no delete endpoint.

Service functions live alongside the existing `list_systems`/`list_payers`/
etc. in `recording_service.py`: `list_form_options`, `create_form_option`,
`update_form_option`.

## Frontend

**Recording form (consumer side):** `RecordingFormSectionA.tsx` stops
importing `CLIENT_OPTIONS`/`ACTION_OPTIONS`/`EHR_OPTIONS` from
`recordingFormSchema.ts` for its three dropdowns, and instead calls a new
`useFormOptions(lob, category)` React Query hook (`frontend/src/lib/
masterData.ts` or a new `formOptions.ts`, matching the existing
`useSystems`/`usePayers` hook style) against `GET /form-options`. The
`SearchableSelect` component and its search/filter behavior are unchanged —
only where `options` comes from changes. `SPECIFICITY_OPTIONS` stays a
local constant (non-goal).

**Admin page (management side):** new page, e.g.
`frontend/src/pages/dev/FormOptionsPage.tsx` — the first admin-CRUD page in
the app, so it sets the pattern rather than following one. Layout: LOB tabs
(AR/EV) → category sub-tabs (Client/Action/EHR) → a table of
`{label, active}` rows with an inline toggle for active/inactive and an
"Add option" row (text input + submit). The page itself, and its nav link,
render only when `/auth/me`'s `role` is `owner` or `admin`; a non-admin
hitting the route directly gets a 403 from the API and an inline "not
authorized" message (no client-side route guard needed beyond that, since
the API already enforces it).

## Testing

- Backend: `test_recording_service.py`-style tests for
  `list_form_options`/`create_form_option`/`update_form_option` — create,
  duplicate-label 409, deactivate-then-list-excludes-it,
  list-all-includes-inactive, org-scoping (one org's options never appear
  for another org).
- API: endpoint tests asserting 403 for a `member`/no-job-function user on
  the three mutating endpoints, 200 for `owner`.
- Migration: a test or manual check that every string currently in
  `CLIENT_OPTIONS`/`ACTION_OPTIONS`/`EHR_OPTIONS` (AR and EV) exists as a
  seeded row after `alembic upgrade head`.
- Frontend: `recordingFormSchema.test.ts`-style test updated/added to cover
  `useFormOptions` returning the dropdown's option list; a smoke test for
  the admin page's add/deactivate interactions.
