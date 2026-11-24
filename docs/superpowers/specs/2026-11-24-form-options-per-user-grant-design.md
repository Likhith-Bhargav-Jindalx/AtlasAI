# Per-user grant for Form options access

## Problem

`/form-options` management (create/update/list-all) is gated to `role in
("owner", "admin")` (`require_job_function()` with no arguments, PR #28).
There is no way to let a specific `member`-role user manage recording-form
dropdown options without promoting them to `admin` — which would also hand
them every other owner/admin-gated action in the app (heal approvals,
etc.).

## Goal

A specific user, regardless of role, can be individually granted access to
manage Form options. Owner and admin keep today's default access
unchanged — this is purely additive.

## Non-goals

- No change to any other owner/admin-gated endpoint — this grant is scoped
  to Form options only.
- No admin UI for granting it. CLI/DB only, matching how `role` and
  `job_function` are already set only via the `create-user` CLI command —
  no user-management UI exists anywhere in the app today.
- No revocation UI either, but the CLI command supports `--revoke`.

## Design

**Data model**: add `can_manage_form_options: bool` (default `False`) to
`User` (`engine/src/atlas_engine/db/models/identity.py`), via a migration.

**Access rule**: new dependency `require_form_options_access()`
(`engine/src/atlas_engine/auth/deps.py`, alongside `require_job_function`)
replaces `require_job_function()` on the three mutating/all `/form-options`
endpoints (`GET /form-options/all`, `POST /form-options`,
`PATCH /form-options/{id}`). Passes when
`user.role in ("owner", "admin") or user.can_manage_form_options`.

**Granting it**: new CLI command
`atlas grant-form-options-access --user-id <id>` (sets the flag `True`) and
`atlas grant-form-options-access --user-id <id> --revoke` (sets it
`False`), in `engine/src/atlas_engine/cli.py` alongside `create-user`.

**Frontend**: `MeResponse` (`frontend/src/lib/api.ts`) gains
`can_manage_form_options: boolean`. `RequireAdminAccess`
(`frontend/src/auth/RequireAdminAccess.tsx`) and the sidebar nav-link
filter in `AppLayout.tsx` both change their check from
`role === 'owner' || role === 'admin'` to
`role === 'owner' || role === 'admin' || can_manage_form_options`.

## Testing

- Backend: a service/API test that a `member`-role user with
  `can_manage_form_options=True` passes `require_form_options_access()`
  (200 on `POST`/`PATCH`/`GET /all`), and that a plain `member` without the
  flag still gets 403. Existing owner/admin tests continue to pass
  unchanged.
- Frontend: extend `RequireAdminAccess.test.tsx` with a case for
  `role: 'member', can_manage_form_options: true` reaching the route.
