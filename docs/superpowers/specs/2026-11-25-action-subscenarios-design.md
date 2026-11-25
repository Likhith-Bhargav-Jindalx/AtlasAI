# Subscenarios per action

## Problem

The "Start new recording" modal's Section A (`RecordingFormSectionA.tsx`)
has a "Select Action" dropdown backed by `form_options` (category=`action`,
see `docs/superpowers/specs/2026-11-24-recording-form-options-crud-design.md`).
That design explicitly deferred "nested category/sub-scenario structure
(e.g. 'Bundled Denial → Corrected Claim')" as a non-goal. This spec adds it:
some actions (e.g. "Denial Correction") have multiple named sub-scenarios;
most don't need any.

## Goals

- A new lookup table, FK'd to the specific `form_options` row (category
  `action`) it belongs to — not a third generic category on `form_options`
  itself, since subscenarios only ever apply to actions and are scoped to
  one specific action, not to a LOB/category pair.
- "Start new recording" gets a "Select Subscenario" dropdown directly below
  "Select Action", populated with that action's subscenarios. If the
  chosen action has none, the field doesn't appear at all.
- Owners/admins (and anyone with `can_manage_form_options`, matching
  `form_options`) can add/deactivate subscenarios per action from the
  admin page — same pattern as `form_options`, nested one level.
- The chosen subscenario persists with the recording request, the same way
  `action`/`ehr` already do (plain text column, not a FK column on
  `recording_requests` — see "Data model" for why).
- Optional field: no action is ever blocked from submitting without one,
  even if it has subscenarios (matches `client`/`action`/`ehr` today, none
  of which are hard-required — `canSubmit` only checks `targetUrl`).

## Non-goals

- No subscenarios for `client`/`ehr` categories — this is action-only, per
  the explicit ask.
- No change to `form_options`' own shape, categories, or endpoints.
- No multi-level nesting beyond one (subscenario of subscenario).
- No backfill/seed migration — nothing existed before this, so the table
  starts empty; admins add rows through the new admin UI.

## Data model

```python
# engine/src/atlas_engine/db/models/masterdata.py (new model, same file as FormOption)

class Subscenario(Base):
    __tablename__ = "subscenarios"
    __table_args__ = (UniqueConstraint("action_id", "label"),)

    id: Mapped[uuid.UUID]            # PK, default uuid4
    action_id: Mapped[uuid.UUID]     # FK -> form_options.id, ondelete CASCADE
    label: Mapped[str]
    active: Mapped[bool]             # default True
    created_at: Mapped[datetime]

    action: Mapped["FormOption"] = relationship()
```

No `org_id`/`lob` columns: a subscenario's org/LOB scope is inherited
through `action_id → form_options.org_id/lob`, so there's nothing to
duplicate or let drift out of sync. The FK's target row is expected (and
service-layer validated, not DB-constrained) to have `category == "action"`
— a `client`/`ehr` option id is rejected with 400, not silently accepted.

A migration creates the table only. No seed data — see Non-goals.

`recording_requests` gets one new column:

```python
subscenario: Mapped[str | None] = mapped_column(default=None)
```

Plain text, following the exact precedent of `action`/`ehr` on the same
model (see `requests.py:46,57`) — the request stores the chosen *label*,
not a foreign key to the `subscenarios` row. This matches how `action`
itself isn't a FK into `form_options` either: the dropdown's backing table
can be edited/relabeled independently of what past requests recorded.

## API

New endpoints in `api.py`, alongside the existing `form_options` ones,
same `require_form_options_access()` guard for mutations:

- `GET /subscenarios?action_id=` — any authenticated user; active rows
  only. Used by the recording form once an action is selected.
- `GET /subscenarios/all?action_id=` — owner/admin/`can_manage_form_options`
  only; all rows including inactive. Used by the admin page.
- `POST /subscenarios` — owner/admin/`can_manage_form_options` only. Body
  `{action_id, label}`. 400 if `action_id` doesn't resolve to a
  `form_options` row with `category="action"`. 409 on duplicate
  `(action_id, label)`.
- `PATCH /subscenarios/{id}` — owner/admin/`can_manage_form_options` only.
  Body `{label?, active?}`. No delete endpoint (deactivate only, matching
  `form_options`).

Service functions live alongside `list_form_options`/`create_form_option`/
`update_form_option` in `recording_service.py`: `list_subscenarios`,
`create_subscenario`, `update_subscenario`.

`create_recording_request` gains a `subscenario: str | None = None`
parameter, stored as-is. `list_recording_requests` and the two recording
serialization dicts (`recording_service.py:364` and `:490`) each gain a
`"subscenario"` key next to the existing `"action"` key.

`CreateRecordingRequestBody` (api.py) and `RecordingRequestSummary`/
`CreateRecordingRequestBody` (`frontend/src/lib/recordingRequests.ts`) each
gain a matching optional `subscenario` field.

## Frontend

**New hook file** `frontend/src/lib/subscenarios.ts`, mirroring
`formOptions.ts`'s shape exactly: `useSubscenarios(actionId)` (active only,
`enabled: !!actionId`), `useAllSubscenarios(actionId)`,
`useCreateSubscenario()`, `useUpdateSubscenario()`.

**Recording form (`RecordingFormSectionA.tsx`):** new `subscenario` state.
`actionId` is resolved from `actionOptions.find(o => o.label === action)?.id`.
`useSubscenarios(actionId)` backs a new `SearchableSelect` rendered directly
below "Select Action", but only when its `data` is non-empty — an action
with zero subscenarios shows no field at all, not an empty dropdown.
Resets (along with `action`) whenever `lob` or `action` changes.
`SectionAValues` gains `subscenario: string`; `InboxPage.tsx`'s
`handleCreate` passes `subscenario: values.subscenario || undefined`
alongside the existing `action` line.

**Admin page (`FormOptionsPage.tsx`):** when `category === 'action'`, each
row gets an expand toggle. Expanding reveals a nested table + add-input for
that action's subscenarios, built from the same
`useAllSubscenarios`/`useCreateSubscenario`/`useUpdateSubscenario` hooks,
reusing the existing table/input markup pattern (not a new generalized
component — the nesting is one level and specific to actions, not worth
abstracting yet).

## Testing

- Backend: `list_subscenarios`/`create_subscenario`/`update_subscenario` —
  create, duplicate-label 409, non-action `action_id` 400, deactivate
  excludes from default list, include_inactive includes it.
- API: 403 for a plain `member` on the three mutating endpoints, 200 for
  `owner`.
- `create_recording_request`/`list_recording_requests`: `subscenario`
  round-trips.
- Frontend: subscenario field appears only when the selected action has
  active subscenarios, resets on action change, submits with the request.
