# Per-user Form options grant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a specific user (any role) be individually granted access to manage Form options, without changing today's default owner/admin access.

**Architecture:** A `can_manage_form_options` boolean on `User`, checked by a new `require_form_options_access()` FastAPI dependency alongside the existing role check. Granted via a new CLI command. Surfaced to the frontend through `/auth/me`, consumed by the existing `RequireAdminAccess` guard and nav filter.

**Tech Stack:** Same as the rest of this feature — FastAPI/SQLAlchemy/Alembic, React/TanStack Query/Vitest.

**Spec:** `docs/superpowers/specs/2026-11-24-form-options-per-user-grant-design.md`

## Global Constraints

- Owner and admin keep today's access unchanged — this is additive only, never a restriction.
- No admin UI for granting it. CLI/DB only (matches `role`/`job_function`, which also have no UI).
- Scoped only to Form options — no other owner/admin-gated endpoint changes.

---

### Task 1: Backend — flag, dependency, migration, CLI grant command

**Files:**
- Modify: `engine/src/atlas_engine/db/models/identity.py`
- Modify: `engine/src/atlas_engine/auth/deps.py`
- Modify: `engine/src/atlas_engine/api.py`
- Modify: `engine/src/atlas_engine/cli.py`
- Create: a new Alembic migration under `engine/migrations/versions/`
- Test: `engine/tests/test_api_form_options.py`

**Interfaces:**
- Produces: `User.can_manage_form_options: bool` (default `False`); `CurrentUser.can_manage_form_options: bool`; `require_form_options_access()` (a zero-arg callable returning a FastAPI dependency, same shape as `require_job_function()`); CLI command `atlas grant-form-options-access --user-id <id> [--revoke]`.

- [ ] **Step 1: Add the column**

In `engine/src/atlas_engine/db/models/identity.py`, add to `User` (after `job_function`):

```python
    can_manage_form_options: Mapped[bool] = mapped_column(default=False)
```

- [ ] **Step 2: Generate and write the migration**

```bash
cd engine && source .venv/bin/activate
alembic revision -m "add can_manage_form_options to users"
```

Keep the auto-generated `revision`/`down_revision`/`create_date` lines, replace the rest:

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("can_manage_form_options", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "can_manage_form_options")
```

Run it against both the dev DB and `atlas_test`:

```bash
alembic upgrade head
ATLAS_DATABASE_URL="postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test" alembic upgrade head
```

- [ ] **Step 3: Extend `CurrentUser` and `get_current_user`**

In `engine/src/atlas_engine/auth/deps.py`, add the field to the dataclass and populate it:

```python
@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    org_id: uuid.UUID
    role: str
    job_function: str | None
    can_manage_form_options: bool
```

```python
        return CurrentUser(
            id=user.id, org_id=user.org_id, role=user.role, job_function=user.job_function,
            can_manage_form_options=user.can_manage_form_options,
        )
```

Add the new dependency factory right after `require_job_function`:

```python
def require_form_options_access():
    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in ("owner", "admin") and not user.can_manage_form_options:
            raise HTTPException(status_code=403, detail="Requires owner/admin role or a Form options access grant")
        return user

    return dependency
```

- [ ] **Step 4: Write the failing tests**

Append to `engine/tests/test_api_form_options.py`:

```python
def test_post_form_options_allows_a_member_with_an_explicit_grant():
    org_id, member_client = _make_user(role="member", job_function="developer")
    with session_scope() as session:
        user = session.query(User).filter_by(org_id=uuid.UUID(org_id)).one()
        user.can_manage_form_options = True

    response = member_client.post("/form-options", json={"lob": "AR", "category": "action", "label": "Granted Create"})
    assert response.status_code == 200


def test_get_form_options_all_allows_a_member_with_an_explicit_grant():
    org_id, member_client = _make_user(role="member", job_function="developer")
    with session_scope() as session:
        user = session.query(User).filter_by(org_id=uuid.UUID(org_id)).one()
        user.can_manage_form_options = True

    response = member_client.get("/form-options/all?lob=AR&category=action")
    assert response.status_code == 200


def test_post_form_options_still_403_for_a_member_without_a_grant():
    _, member_client = _make_user(role="member", job_function="developer")
    response = member_client.post("/form-options", json={"lob": "AR", "category": "action", "label": "Ungranted"})
    assert response.status_code == 403
```

- [ ] **Step 5: Run the tests to verify they fail**

```bash
ATLAS_DATABASE_URL="postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test" .venv/bin/pytest tests/test_api_form_options.py -k grant -v
```

Expected: FAIL (the first two 403, since the endpoints still use `require_job_function()`; the `_make_user` helper's returned `org_id` lets the test look up the just-created user by org, since `_make_user` only returns `(org_id, client)` — no change needed there).

- [ ] **Step 6: Switch the three endpoints to the new dependency**

In `engine/src/atlas_engine/api.py`, change `Depends(require_job_function())` to `Depends(require_form_options_access())` on all three: `list_all_form_options_endpoint`, `create_form_option_endpoint`, `update_form_option_endpoint` (lines ~306-334). Add `require_form_options_access` to the existing `from atlas_engine.auth.deps import CurrentUser, get_current_user, require_job_function` import line.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
ATLAS_DATABASE_URL="postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test" .venv/bin/pytest tests/test_api_form_options.py -v
```

Expected: PASS (all, including the pre-existing owner/admin/403 tests — unchanged by this dependency swap since it preserves the same role check and adds the flag as an alternative, not a replacement).

- [ ] **Step 8: Update `_me_response_for` and `MeResponse`**

In `engine/src/atlas_engine/api.py`:

```python
class MeResponse(BaseModel):
    user_id: str
    org_id: str
    email: str
    role: str
    job_function: str | None = None
    can_manage_form_options: bool = False


def _me_response_for(user: User) -> MeResponse:
    return MeResponse(
        user_id=str(user.id), org_id=str(user.org_id), email=user.email,
        role=user.role, job_function=user.job_function,
        can_manage_form_options=user.can_manage_form_options,
    )
```

- [ ] **Step 9: Add the CLI grant command**

In `engine/src/atlas_engine/cli.py`, after `create_user`:

```python
@main.command("grant-form-options-access")
@click.option("--user-id", required=True)
@click.option("--revoke", is_flag=True, default=False)
def grant_form_options_access(user_id: str, revoke: bool) -> None:
    """Grant (or --revoke) a specific user access to manage Form options, independent of role."""
    with session_scope() as session:
        user = session.get(User, uuid.UUID(user_id))
        if user is None:
            raise click.ClickException(f"No such user: {user_id}")
        user.can_manage_form_options = not revoke
        session.add(user)
        click.echo(f"{'Revoked' if revoke else 'Granted'} Form options access for {user.email}")
```

- [ ] **Step 10: Run the full backend form-options and auth test files**

```bash
ATLAS_DATABASE_URL="postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test" .venv/bin/pytest tests/test_api_form_options.py tests/test_api_auth.py tests/test_api_auth_guards.py -v
```

Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add engine/src/atlas_engine/db/models/identity.py engine/src/atlas_engine/auth/deps.py engine/src/atlas_engine/api.py engine/src/atlas_engine/cli.py engine/migrations/versions/ engine/tests/test_api_form_options.py
git commit -m "feat: let a specific user be granted Form options access regardless of role"
```

---

### Task 2: Frontend — surface the grant and update the guard/nav

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/auth/RequireAdminAccess.tsx`
- Modify: `frontend/src/layout/AppLayout.tsx`
- Modify: `frontend/src/auth/RequireAdminAccess.test.tsx`

**Interfaces:**
- Consumes: `MeResponse.can_manage_form_options` (Task 1, backend).

- [ ] **Step 1: Write the failing test**

In `frontend/src/auth/RequireAdminAccess.test.tsx`, change the mocked `/auth/me` response to include a module-level `canManage` variable (default `false`) alongside the existing `role` variable, and add a new test:

```ts
let canManage = false
```

(declared next to the existing `let role = 'member'`), then update the `/auth/me` mock body to `{ ..., can_manage_form_options: canManage }`, and add:

```tsx
describe('RequireAdminAccess with an explicit grant', () => {
  it('lets a granted member through', async () => {
    role = 'member'
    canManage = true
    renderAtFormOptions()
    expect(await screen.findByText('Form options content')).toBeInTheDocument()
  })
})
```

Reset `canManage = false` in the existing `beforeEach` alongside `role = 'member'` — wait, `role` isn't reset in `beforeEach` today either (it's reassigned at the start of each `it`), so just make sure every existing `it` that doesn't set `canManage` explicitly still passes by defaulting the module-level `let canManage = false` and only the new test sets it `true`.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npx vitest run RequireAdminAccess.test.tsx
```

Expected: FAIL (new test's redirect still fires — `RequireAdminAccess` doesn't check `can_manage_form_options` yet).

- [ ] **Step 3: Update the frontend types and guard**

In `frontend/src/lib/api.ts`, add to `MeResponse`:

```ts
export interface MeResponse {
  user_id: string
  org_id: string
  email: string
  role: string
  job_function: string | null
  can_manage_form_options: boolean
}
```

In `frontend/src/auth/RequireAdminAccess.tsx`:

```tsx
export function RequireAdminAccess() {
  const { user } = useAuth()
  if (user && user.role !== 'owner' && user.role !== 'admin' && !user.can_manage_form_options) {
    return <Navigate to="/dev" replace />
  }
  return <Outlet />
}
```

- [ ] **Step 4: Update the nav filter**

In `frontend/src/layout/AppLayout.tsx`, change the filter predicate:

```tsx
            .filter((item) => item.to !== '/dev/form-options' || user?.role === 'owner' || user?.role === 'admin' || user?.can_manage_form_options)
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd frontend && npx vitest run RequireAdminAccess.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Run the full frontend test suite**

```bash
cd frontend && npx vitest run
```

Expected: PASS (no regressions — every other test's mocked `/auth/me` response omits `can_manage_form_options`, which parses as `undefined`/falsy and behaves exactly as before).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/auth/RequireAdminAccess.tsx frontend/src/auth/RequireAdminAccess.test.tsx frontend/src/layout/AppLayout.tsx
git commit -m "feat: surface per-user Form options grant in the frontend guard and nav"
```
