# Recording Library Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close six remaining console gaps — an operations-agent recording library, a developer request-claim queue, editable/submittable ad-hoc recordings, server-side role enforcement, heal-approval before/after screenshots, and an LLM cost dashboard — without changing one line of the ported self-healing brain/replay logic.

**Architecture:** Additive-only changes layered on the existing FastAPI + SQLAlchemy engine and React + React Query frontend. Every task follows the codebase's established conventions (thin endpoints wrapping `*_service.py` functions that return `dict`/`list[dict]`; React Query hooks in `lib/*.ts`; Alembic migrations only where a genuinely new column/table is needed).

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, pytest (engine); React, React Query, Vitest + Testing Library (frontend).

**Spec:** `docs/superpowers/specs/2026-08-14-recording-library-completion-design.md`

## Global Constraints

- **Do not modify:** `engine/src/atlas_engine/brain/loop.py`, `engine/src/atlas_engine/brain/repair.py`, `engine/src/atlas_engine/brain/evaluate.py`, `engine/src/atlas_engine/brain/blockers.py`, `engine/src/atlas_engine/capture/codegen.py`'s parsing/trajectory-building logic, `engine/src/atlas_engine/authoring/replay.py`, `engine/src/atlas_engine/codegen/emit.py`. These implement the self-healing core ported from `/Users/apple/Desktop/atlasAI/` and are verified against real portal recordings — no task in this plan touches them.
- **Regression gate:** every task ends with `cd engine && .venv/bin/pytest -q` (197+ tests, must stay green) and, for any task touching frontend code, `cd frontend && npm test && npm run lint && npm run build` (must stay green). Run the full suite, not just the new test file — several tasks touch shared files (`api.py`, `recording_service.py`).
- **Two DB scaffolding rows already exist and are reused, not recreated:** `Recording.user_id` (column exists, never populated — Task 1 populates it) and `UsageLedgerEntry` (table exists, never written to or read from — Task 9 wires it up). Confirm this with `grep -rn "UsageLedgerEntry\|user_id" engine/src/atlas_engine/db/models/` before Task 9 if picking this plan up cold — this fact is why Task 9 needs no migration.
- Role fields: `User.role` is the org tier (`owner`/`admin`/`member`); `User.job_function` (`operations_agent`/`developer`) is what distinguishes an agent from a developer and is what `frontend/src/layout/RoleContext.tsx` already keys off. Task 6's enforcement gates on `job_function`, with `role in ("owner", "admin")` as a bypass.
- CORS in `engine/src/atlas_engine/api.py` already allows `http://localhost:5183` with credentials — no change needed for any task; the frontend's `apiFetch` (`frontend/src/lib/api.ts`) already sends `credentials: 'include'`.

---

### Task 1: Thread `user_id` through recording capture, filter the library by it

**Problem:** `Recording.user_id` exists as a column but is never set by `persist_recording` or `start_live_capture` — every recording has `user_id = NULL` today. Task 2 (agent library) needs to filter recordings by "the signed-in agent's own", which is impossible until this is fixed.

**Files:**
- Modify: `engine/src/atlas_engine/recording_service.py` (`persist_recording`, `list_recordings`)
- Modify: `engine/src/atlas_engine/live_capture.py` (`_LiveCaptureSession.__init__`/`_run`, `start_live_capture`)
- Modify: `engine/src/atlas_engine/api.py` (`StartRecordingBody`, `start_recording_endpoint`, `list_recordings_endpoint`)
- Modify: `frontend/src/pages/agent/LiveRecordingPage.tsx`
- Modify: `frontend/src/lib/recordings.ts` (`useStartRecording`, `useRecordings`)
- Test: `engine/tests/test_recording_service.py`, `engine/tests/test_live_capture.py`, `engine/tests/test_api_recordings.py`

**Interfaces:**
- Produces: `persist_recording(..., user_id: str | None = None)`; `list_recordings(org_id, status=None, user_id=None) -> list[dict]`; `start_live_capture(org_id, start_url, *, request_id=None, role="demo", user_id=None) -> uuid.UUID`.

- [ ] **Step 1: Write the failing backend tests**

Add to `engine/tests/test_recording_service.py` (check the top of the file for its existing imports of `Organization`, `session_scope`, `persist_recording`, `uuid` — reuse them):

```python
def test_persist_recording_stores_user_id(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="User Id Org", slug=f"user-id-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        user = User(org_id=org.id, email="agent@example.com", password_hash="x", job_function="operations_agent")
        session.add(user)
        session.flush()
        user_id = str(user.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [], user_id=user_id)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert str(recording.user_id) == user_id


def test_list_recordings_filters_by_user_id(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Filter User Org", slug=f"filter-user-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent_a = User(org_id=org.id, email="a@example.com", password_hash="x", job_function="operations_agent")
        agent_b = User(org_id=org.id, email="b@example.com", password_hash="x", job_function="operations_agent")
        session.add_all([agent_a, agent_b])
        session.flush()
        agent_a_id, agent_b_id = str(agent_a.id), str(agent_b.id)

    start_url = f"{fixture_portal_url}/login.html"
    recording_a = persist_recording(org_id, start_url, [], user_id=agent_a_id)
    persist_recording(org_id, start_url, [], user_id=agent_b_id)

    results = list_recordings(org_id, user_id=agent_a_id)
    assert [r["id"] for r in results] == [str(recording_a)]
```

This test file's existing imports need `User` added to its `from atlas_engine.db.models import ...` line if not already present — check first.

Add to `engine/tests/test_live_capture.py`:

```python
def test_start_live_capture_stores_user_id(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Live Capture User Org", slug=f"live-capture-user-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        user = User(org_id=org.id, email="live@example.com", password_hash="x", job_function="operations_agent")
        session.add(user)
        session.flush()
        user_id = str(user.id)

    capture_session_id = start_live_capture(org_id, f"{fixture_portal_url}/login.html", user_id=user_id)
    recording_id = stop_live_capture(capture_session_id)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert str(recording.user_id) == user_id
```

Add `User` to this file's `from atlas_engine.db.models import Organization, Recording, VideoAsset` import.

Add to `engine/tests/test_api_recordings.py`:

```python
def test_list_recordings_endpoint_filters_by_user_id(fixture_portal_url):
    from atlas_engine.db.models import User

    with session_scope() as session:
        org = Organization(name="API User Filter Org", slug=f"api-user-filter-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        user = User(org_id=org.id, email="c@example.com", password_hash="x", job_function="operations_agent")
        session.add(user)
        session.flush()
        user_id = str(user.id)

    start_url = f"{fixture_portal_url}/login.html"
    mine = persist_recording(org_id, start_url, [], user_id=user_id)
    persist_recording(org_id, start_url, [])  # someone else's / unattributed

    client = TestClient(app)
    response = client.get(f"/recordings?org_id={org_id}&user_id={user_id}")
    assert response.status_code == 200
    assert [r["id"] for r in response.json()] == [str(mine)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_recording_service.py tests/test_live_capture.py tests/test_api_recordings.py -k user_id -v`
Expected: FAIL — `persist_recording()`/`start_live_capture()` raise `TypeError: unexpected keyword argument 'user_id'`, and `list_recordings()` ignores the `user_id` kwarg (`TypeError` there too, since it doesn't accept it yet).

- [ ] **Step 3: Implement `persist_recording` and `list_recordings`**

In `engine/src/atlas_engine/recording_service.py`, add `user_id: str | None = None` to `persist_recording`'s signature (after `request_id`), and inside the `Recording(...)` construction add `user_id=uuid.UUID(user_id) if user_id else None,`.

Change `list_recordings`'s signature to `def list_recordings(org_id: str, status: str | None = None, user_id: str | None = None) -> list[dict]:` and add right after the existing `status` filter:

```python
        if user_id is not None:
            query = query.filter_by(user_id=uuid.UUID(user_id))
```

- [ ] **Step 4: Implement `start_live_capture` threading**

In `engine/src/atlas_engine/live_capture.py`, change `_LiveCaptureSession.__init__` to accept `user_id: str | None` and store `self.user_id = user_id`. In `_run`, add `user_id=self.user_id,` to the `persist_recording(...)` call. Change `start_live_capture`'s signature to:

```python
def start_live_capture(
    org_id: str, start_url: str, *, request_id: uuid.UUID | None = None, role: str = "demo",
    user_id: str | None = None,
) -> uuid.UUID:
    session_id = uuid.uuid4()
    session = _LiveCaptureSession(org_id, start_url, request_id, role, user_id)
    with _registry_lock:
        _sessions[session_id] = session
    session.start()
    return session_id
```

- [ ] **Step 5: Wire the API endpoints**

In `engine/src/atlas_engine/api.py`, add `user_id: str | None = None` to `StartRecordingBody`, pass it through in `start_recording_endpoint`:

```python
    capture_session_id = start_live_capture(
        body.org_id, body.start_url,
        request_id=uuid.UUID(body.request_id) if body.request_id else None,
        role=body.role, user_id=body.user_id,
    )
```

Change `list_recordings_endpoint` to `def list_recordings_endpoint(org_id: str, status: str | None = None, user_id: str | None = None) -> list[dict]: return list_recordings(org_id, status=status, user_id=user_id)`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_recording_service.py tests/test_live_capture.py tests/test_api_recordings.py -v`
Expected: PASS

- [ ] **Step 7: Wire the frontend to send `user_id`**

In `frontend/src/lib/recordings.ts`, add `user_id?: string` to `useStartRecording`'s body type, and add an optional `userId` parameter to `useRecordings`:

```typescript
export function useRecordings(orgId: string, status?: string, userId?: string) {
  return useQuery({
    queryKey: ['recordings', orgId, status, userId],
    queryFn: () => {
      const params = new URLSearchParams({ org_id: orgId })
      if (status) params.set('status', status)
      if (userId) params.set('user_id', userId)
      return apiFetch<RecordingSummary[]>(`/recordings?${params}`)
    },
  })
}
```

And change `useStartRecording`'s `mutationFn` parameter type to `(body: { org_id: string; start_url: string; request_id?: string; role?: string; user_id?: string })`.

In `frontend/src/pages/agent/LiveRecordingPage.tsx`, add `user_id: user!.user_id` to the `startRecording.mutateAsync({...})` call at line 26.

- [ ] **Step 8: Run the frontend suite**

Run: `cd frontend && npm test && npm run lint && npm run build`
Expected: PASS (no existing test exercises `LiveRecordingPage`, so this is a green no-op check that nothing broke)

- [ ] **Step 9: Full regression + commit**

Run: `cd engine && .venv/bin/pytest -q`
Expected: all pass (197+)

```bash
git add engine/src/atlas_engine/recording_service.py engine/src/atlas_engine/live_capture.py engine/src/atlas_engine/api.py engine/tests/test_recording_service.py engine/tests/test_live_capture.py engine/tests/test_api_recordings.py frontend/src/lib/recordings.ts frontend/src/pages/agent/LiveRecordingPage.tsx
git commit -m "feat: thread user_id through recording capture so recordings can be filtered by owner"
```

---

### Task 2: Operations-agent recording library page

**Files:**
- Create: `frontend/src/pages/agent/AgentLibraryPage.tsx`
- Create: `frontend/src/pages/agent/AgentLibraryPage.test.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/layout/AppLayout.tsx`

**Interfaces:**
- Consumes: `useRecordings(orgId, status?, userId?)` from Task 1, `RecordingSummary` from `lib/recordings.ts`, `StatusPill`, `formatDuration`/`formatRelativeTime`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/pages/agent/AgentLibraryPage.test.tsx
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AgentLibraryPage } from './AgentLibraryPage'

const RECORDINGS = [
  {
    id: 'rec-1', status: 'captured', role: 'demo', request_id: null, request_title: null,
    portal_domain: 'portal.example.com', duration_seconds: 42, has_video: true,
    has_codegen_script: true, created_at: new Date().toISOString(),
  },
]

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path.startsWith('/recordings?')) return Promise.resolve(jsonResponse(RECORDINGS))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {}, removeItem: () => {} })
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/agent/library']}>
        <Routes>
          <Route path="/agent/library" element={<AgentLibraryPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('AgentLibraryPage', () => {
  it('renders recording cards without a codegen badge', async () => {
    renderPage()
    expect(await screen.findByText('portal.example.com')).toBeInTheDocument()
    expect(screen.queryByText(/playwright script/i)).not.toBeInTheDocument()
  })
})
```

Note: this component needs `useAuth()` for `org_id`/`user_id` — check `frontend/src/auth/AuthContext.tsx`'s `useAuth` hook; if the test above fails because `useAuth` throws (`must be used within AuthProvider`), wrap `renderPage`'s tree in the real `AuthProvider` instead of stubbing `useAuth` directly (it internally calls `fetchMe`, which hits `/auth/me` — add that route to the fetch stub returning a fake `MeResponse`, matching the pattern `AuthoringPage.test.tsx` uses for its own dependencies). Concretely, add to the stub:

```typescript
    if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'user-1', org_id: 'org-1', email: 'a@x.com', role: 'member', job_function: 'operations_agent' }))
```

and wrap the render tree: `<AuthProvider><MemoryRouter>...</MemoryRouter></AuthProvider>` (import `AuthProvider` from `'../../auth/AuthContext'`).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/agent/AgentLibraryPage.test.tsx`
Expected: FAIL — `Cannot find module './AgentLibraryPage'`

- [ ] **Step 3: Implement `AgentLibraryPage`**

```tsx
// frontend/src/pages/agent/AgentLibraryPage.tsx
import { useNavigate } from 'react-router-dom'
import { VideoOff } from 'lucide-react'
import { useAuth } from '../../auth/AuthContext'
import { useRecordings } from '../../lib/recordings'
import { StatusPill } from '../../components/StatusPill'
import { formatDuration, formatRelativeTime } from '../../lib/format'

export function AgentLibraryPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const { data: recordings, isLoading } = useRecordings(user!.org_id, undefined, user!.user_id)

  if (isLoading) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  return (
    <div className="p-9 pb-16 flex flex-col gap-7 max-w-[1180px]">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-display text-[30px] font-semibold tracking-tight m-0">Recording library</h1>
        <div className="text-sm text-atlas-body">Your past recordings — video and steps.</div>
      </div>

      <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-5">
        {(recordings ?? []).map((r) => (
          <button
            key={r.id}
            onClick={() => navigate(`/agent/recordings/${r.id}`)}
            className="text-left bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden hover:shadow-lg hover:-translate-y-0.5 transition-all"
          >
            <div className="aspect-video bg-[#e8eae8] relative flex items-center justify-center">
              {!r.has_video && <VideoOff size={24} className="text-atlas-muted" />}
              <div className="absolute right-2.5 bottom-2.5 text-[11px] text-white bg-black/55 px-2 py-0.5 rounded-full tabular-nums">
                {formatDuration(r.duration_seconds)}
              </div>
            </div>
            <div className="p-4 flex flex-col gap-2.5">
              <div className="flex items-start justify-between gap-2.5">
                <div className="text-[14.5px] font-medium tracking-tight">{r.request_title ?? r.portal_domain ?? 'Recording'}</div>
                <StatusPill status={r.status} />
              </div>
              <div className="text-[12.5px] text-atlas-body">{r.portal_domain}</div>
              <div className="text-[12px] text-atlas-muted pt-1.5 border-t border-atlas-rowBorder">
                {formatRelativeTime(r.created_at)}
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/agent/AgentLibraryPage.test.tsx`
Expected: PASS

- [ ] **Step 5: Wire the route and nav entry**

In `frontend/src/App.tsx`, add the import `import { AgentLibraryPage } from './pages/agent/AgentLibraryPage'` and the route `<Route path="/agent/library" element={<AgentLibraryPage />} />` next to the existing `/agent/recordings/:recordingId` route.

In `frontend/src/layout/AppLayout.tsx`, add `{ to: '/agent/library', label: 'Recording library' }` to `NAV.agent`.

- [ ] **Step 6: Full frontend suite + commit**

Run: `cd frontend && npm test && npm run lint && npm run build`
Expected: PASS

```bash
git add frontend/src/pages/agent/AgentLibraryPage.tsx frontend/src/pages/agent/AgentLibraryPage.test.tsx frontend/src/App.tsx frontend/src/layout/AppLayout.tsx
git commit -m "feat: add operations-agent recording library (video + steps, no code)"
```

---

### Task 3: Developer request-queue / claim screen

**Files:**
- Create: `frontend/src/pages/dev/RequestQueuePage.tsx`
- Create: `frontend/src/pages/dev/RequestQueuePage.test.tsx`
- Modify: `frontend/src/lib/recordingRequests.ts` (add `useClaimRecordingRequest`)
- Modify: `frontend/src/App.tsx`, `frontend/src/layout/AppLayout.tsx`

**Interfaces:**
- Consumes: `useRecordingRequests(orgId, status?)`, `RecordingRequestSummary` (from `lib/recordingRequests.ts`); existing backend `POST /recording-requests/{id}/claim` (body `{developer_id}`).
- Produces: `useClaimRecordingRequest(): UseMutationResult<void, ApiError, { requestId: string; developerId: string }>`.

- [ ] **Step 1: Write the failing test for `useClaimRecordingRequest`**

Add to a new `frontend/src/lib/recordingRequests.test.ts` (no such file exists yet — create it):

```typescript
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { useClaimRecordingRequest } from './recordingRequests'

let queryClient: QueryClient

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/recording-requests/req-1/claim' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string)
      expect(body).toEqual({ developer_id: 'dev-1' })
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ ok: true }) })
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

describe('useClaimRecordingRequest', () => {
  it('POSTs the developer id and invalidates the requests query', async () => {
    const { result } = renderHook(() => useClaimRecordingRequest(), { wrapper })
    result.current.mutate({ requestId: 'req-1', developerId: 'dev-1' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })
})
```

(This is a `.ts` file containing JSX in `wrapper` — name it `recordingRequests.test.tsx` instead so the JSX parses.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/recordingRequests.test.tsx`
Expected: FAIL — `useClaimRecordingRequest is not a function`

- [ ] **Step 3: Implement `useClaimRecordingRequest`**

Add to `frontend/src/lib/recordingRequests.ts`:

```typescript
export function useClaimRecordingRequest() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ requestId, developerId }: { requestId: string; developerId: string }) =>
      apiFetch<{ ok: boolean }>(`/recording-requests/${requestId}/claim`, {
        method: 'POST',
        body: JSON.stringify({ developer_id: developerId }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['recording-requests'] }),
  })
}
```

Also add `claimed_by: string | null` is already on `RecordingRequestSummary` — no change needed there.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/recordingRequests.test.tsx`
Expected: PASS

- [ ] **Step 5: Write the failing page test**

```tsx
// frontend/src/pages/dev/RequestQueuePage.test.tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../auth/AuthContext'
import { RequestQueuePage } from './RequestQueuePage'

const OPEN_REQUEST = { id: 'req-1', title: 'Check eligibility', target_url: 'https://portal.example.com', status: 'open', claimed_by: null }
let claimed = false

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  claimed = false
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'dev-1', org_id: 'org-1', email: 'd@x.com', role: 'member', job_function: 'developer' }))
    if (path.startsWith('/recording-requests?')) {
      return Promise.resolve(jsonResponse(claimed ? [{ ...OPEN_REQUEST, claimed_by: 'dev-1' }] : [OPEN_REQUEST]))
    }
    if (path === '/recording-requests/req-1/claim' && init?.method === 'POST') {
      claimed = true
      return Promise.resolve(jsonResponse({ ok: true }))
    }
    if (path.startsWith('/recordings?')) return Promise.resolve(jsonResponse([]))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={['/dev/requests']}>
          <RequestQueuePage />
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  )
}

describe('RequestQueuePage', () => {
  it('claims a request and moves it into the claimed-by-me section', async () => {
    renderPage()
    const claimButton = await screen.findByRole('button', { name: /claim/i })
    fireEvent.click(claimButton)
    await waitFor(() => expect(screen.getByText(/waiting for a recording/i)).toBeInTheDocument())
  })
})
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/dev/RequestQueuePage.test.tsx`
Expected: FAIL — `Cannot find module './RequestQueuePage'`

- [ ] **Step 7: Implement `RequestQueuePage`**

```tsx
// frontend/src/pages/dev/RequestQueuePage.tsx
import { useAuth } from '../../auth/AuthContext'
import { useClaimRecordingRequest, useRecordingRequests } from '../../lib/recordingRequests'
import { useRecordings } from '../../lib/recordings'
import { StatusPill } from '../../components/StatusPill'

export function RequestQueuePage() {
  const { user } = useAuth()
  const orgId = user!.org_id
  const { data: openRequests } = useRecordingRequests(orgId, 'open')
  const { data: submittedRequests } = useRecordingRequests(orgId, 'submitted')
  const { data: recordings } = useRecordings(orgId)
  const claim = useClaimRecordingRequest()

  const requests = [...(openRequests ?? []), ...(submittedRequests ?? [])]
  const unclaimed = requests.filter((r) => !r.claimed_by)
  const mine = requests.filter((r) => r.claimed_by === user!.user_id)

  function recordingFor(requestId: string) {
    return (recordings ?? []).find((r) => r.request_id === requestId)
  }

  return (
    <div className="p-9 pb-16 flex flex-col gap-7 max-w-[1180px]">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-display text-[30px] font-semibold tracking-tight m-0">Request queue</h1>
        <div className="text-sm text-atlas-body">Claim a request, then wait for the agent's recording to land.</div>
      </div>

      <div className="flex flex-col gap-3">
        <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Open · {unclaimed.length}</div>
        {unclaimed.map((r) => (
          <div key={r.id} className="bg-atlas-card border border-atlas-border rounded-2xl px-[22px] py-[18px] flex items-center justify-between gap-5">
            <div className="flex flex-col gap-1">
              <div className="text-sm font-medium tracking-tight">{r.title}</div>
              <div className="text-[12.5px] text-atlas-body">{r.target_url}</div>
            </div>
            <div className="flex items-center gap-3">
              <StatusPill status={r.status} />
              <button
                onClick={() => claim.mutate({ requestId: r.id, developerId: user!.user_id })}
                disabled={claim.isPending}
                className="px-[18px] py-2 rounded-full border border-atlas-borderHover text-atlas-primary font-medium text-[12.5px] hover:bg-atlas-accentSubtle disabled:opacity-60"
              >
                Claim
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-3">
        <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Claimed by me · {mine.length}</div>
        {mine.map((r) => {
          const recording = recordingFor(r.id)
          return (
            <div key={r.id} className="bg-atlas-card border border-atlas-border rounded-2xl px-[22px] py-[18px] flex items-center justify-between gap-5">
              <div className="flex flex-col gap-1">
                <div className="text-sm font-medium tracking-tight">{r.title}</div>
                <div className="text-[12.5px] text-atlas-body">{r.target_url}</div>
              </div>
              {recording ? (
                <a href={`/dev/recordings/${recording.id}`} className="text-[12.5px] text-atlas-primary font-medium">
                  Open recording →
                </a>
              ) : (
                <span className="text-[12.5px] text-atlas-muted">Waiting for a recording</span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/dev/RequestQueuePage.test.tsx`
Expected: PASS

- [ ] **Step 9: Wire the route and nav entry**

In `frontend/src/App.tsx`, import `RequestQueuePage` and add `<Route path="/dev/requests" element={<RequestQueuePage />} />`.

In `frontend/src/layout/AppLayout.tsx`, add `{ to: '/dev/requests', label: 'Request queue' }` to `NAV.dev`.

- [ ] **Step 10: Full frontend suite + commit**

Run: `cd frontend && npm test && npm run lint && npm run build`
Expected: PASS

```bash
git add frontend/src/pages/dev/RequestQueuePage.tsx frontend/src/pages/dev/RequestQueuePage.test.tsx frontend/src/lib/recordingRequests.ts frontend/src/lib/recordingRequests.test.tsx frontend/src/App.tsx frontend/src/layout/AppLayout.tsx
git commit -m "feat: add developer request-queue/claim screen"
```

---

### Task 4: Editable recording title/description + submit-to-developer (backend)

**Files:**
- Modify: `engine/src/atlas_engine/db/models/portal.py` (`Recording`)
- Create: `engine/migrations/versions/<autogenerated>_add_title_description_to_recordings.py`
- Modify: `engine/src/atlas_engine/recording_service.py` (`get_recording_detail`, new `update_recording_details`, `submit_recording_for_authoring`)
- Modify: `engine/src/atlas_engine/api.py`
- Test: `engine/tests/test_recording_service.py`, `engine/tests/test_api_recordings.py`

**Interfaces:**
- Produces: `update_recording_details(recording_id: uuid.UUID, *, title: str | None = None, description: str | None = None) -> None`; `submit_recording_for_authoring(recording_id: uuid.UUID, submitted_by: uuid.UUID) -> uuid.UUID` (returns the new request id, raises `ValueError` if already linked to a request).

- [ ] **Step 1: Add the columns and generate the migration**

In `engine/src/atlas_engine/db/models/portal.py`, add to `Recording` (after `role`):

```python
    title: Mapped[str | None] = mapped_column(default=None)
    description: Mapped[str | None] = mapped_column(default=None)
```

Run: `cd engine && .venv/bin/alembic revision --autogenerate -m "add title and description to recordings"`

Open the generated file under `migrations/versions/` and confirm it contains exactly:

```python
    op.add_column('recordings', sa.Column('title', sa.String(), nullable=True))
    op.add_column('recordings', sa.Column('description', sa.String(), nullable=True))
```

in `upgrade()` (and the matching `drop_column` calls in `downgrade()`). If autogenerate produced anything else (e.g. it tried to touch unrelated columns), trim the file down to just these two `add_column`/`drop_column` pairs — autogenerate sometimes picks up incidental diffs from the dev database's drift.

Run: `cd engine && .venv/bin/alembic upgrade head`
Expected: migration applies with no errors.

- [ ] **Step 2: Write the failing service tests**

Add to `engine/tests/test_recording_service.py`:

```python
def test_update_recording_details_persists_title_and_description(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Update Details Org", slug=f"update-details-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])
    update_recording_details(recording_id, title="Check eligibility", description="Walks the eligibility flow")

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert recording.title == "Check eligibility"
        assert recording.description == "Walks the eligibility flow"


def test_submit_recording_for_authoring_creates_a_linked_request(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Submit Org", slug=f"submit-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent = User(org_id=org.id, email="submitter@example.com", password_hash="x", job_function="operations_agent")
        session.add(agent)
        session.flush()
        agent_id = agent.id

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])
    update_recording_details(recording_id, title="Check eligibility")

    request_id = submit_recording_for_authoring(recording_id, agent_id)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert recording.request_id == request_id
        request = session.get(RecordingRequest, request_id)
        assert request.status == "submitted"
        assert request.title == "Check eligibility"


def test_submit_recording_for_authoring_rejects_already_linked_recording(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Submit Twice Org", slug=f"submit-twice-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent = User(org_id=org.id, email="submitter2@example.com", password_hash="x", job_function="operations_agent")
        session.add(agent)
        session.flush()
        agent_id = agent.id

    start_url = f"{fixture_portal_url}/login.html"
    request_id = create_recording_request(org_id, str(agent_id), start_url, "Existing request")
    recording_id = persist_recording(org_id, start_url, [], request_id=request_id)

    with pytest.raises(ValueError, match="already linked"):
        submit_recording_for_authoring(recording_id, agent_id)
```

Add `pytest` and `RecordingRequest`/`User` to this test file's imports if not already present.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_recording_service.py -k "update_recording_details or submit_recording_for_authoring" -v`
Expected: FAIL — `ImportError: cannot import name 'update_recording_details'`

- [ ] **Step 4: Implement `update_recording_details` and `submit_recording_for_authoring`**

Add to `engine/src/atlas_engine/recording_service.py` (after `get_recording_detail`):

```python
def update_recording_details(recording_id: uuid.UUID, *, title: str | None = None, description: str | None = None) -> None:
    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        if recording is None:
            raise ValueError(f"No such recording: {recording_id}")
        if title is not None:
            recording.title = title
        if description is not None:
            recording.description = description


def submit_recording_for_authoring(recording_id: uuid.UUID, submitted_by: uuid.UUID) -> uuid.UUID:
    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        if recording is None:
            raise ValueError(f"No such recording: {recording_id}")
        if recording.request_id is not None:
            raise ValueError(f"Recording {recording_id} is already linked to a request")

        profile = session.get(PortalProfile, recording.portal_profile_id)
        target_url = profile.inferred_login_url or f"https://{profile.domain}"

        request = RecordingRequest(
            org_id=recording.org_id,
            requested_by=submitted_by,
            portal_profile_id=recording.portal_profile_id,
            target_url=target_url,
            title=recording.title or "Untitled recording",
            description=recording.description,
            status="submitted",
        )
        session.add(request)
        session.flush()

        recording.request_id = request.id
        return request.id
```

Also update `get_recording_detail`'s returned dict to include `"title": recording.title, "description": recording.description,` (add these two keys next to `"role": recording.role,`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_recording_service.py -k "update_recording_details or submit_recording_for_authoring" -v`
Expected: PASS

- [ ] **Step 6: Add the API endpoints**

In `engine/src/atlas_engine/api.py`, add the import `update_recording_details, submit_recording_for_authoring` to the existing `from atlas_engine.recording_service import (...)` block, and add:

```python
class UpdateRecordingDetailsBody(BaseModel):
    title: str | None = None
    description: str | None = None


@app.patch("/recordings/{recording_id}")
def update_recording_details_endpoint(recording_id: str, body: UpdateRecordingDetailsBody) -> dict:
    try:
        update_recording_details(uuid.UUID(recording_id), title=body.title, description=body.description)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}


class SubmitRecordingBody(BaseModel):
    submitted_by: str


class SubmitRecordingResponse(BaseModel):
    request_id: str


@app.post("/recordings/{recording_id}/submit", response_model=SubmitRecordingResponse)
def submit_recording_endpoint(recording_id: str, body: SubmitRecordingBody) -> SubmitRecordingResponse:
    try:
        request_id = submit_recording_for_authoring(uuid.UUID(recording_id), uuid.UUID(body.submitted_by))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return SubmitRecordingResponse(request_id=str(request_id))
```

Add `title: str | None = None` and `description: str | None = None` to `RecordingDetailResponse`.

- [ ] **Step 7: Write and run the endpoint tests**

Add to `engine/tests/test_api_recordings.py`:

```python
def test_patch_recording_updates_title_and_description(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Patch Org", slug=f"patch-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    client = TestClient(app)
    response = client.patch(f"/recordings/{recording_id}", json={"title": "My flow", "description": "desc"})
    assert response.status_code == 200

    detail = client.get(f"/recordings/{recording_id}").json()
    assert detail["title"] == "My flow"
    assert detail["description"] == "desc"


def test_submit_recording_endpoint_creates_a_request(fixture_portal_url):
    from atlas_engine.db.models import User

    with session_scope() as session:
        org = Organization(name="Submit API Org", slug=f"submit-api-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent = User(org_id=org.id, email="submit-api@example.com", password_hash="x", job_function="operations_agent")
        session.add(agent)
        session.flush()
        agent_id = str(agent.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    client = TestClient(app)
    response = client.post(f"/recordings/{recording_id}/submit", json={"submitted_by": agent_id})
    assert response.status_code == 200
    assert "request_id" in response.json()
```

Run: `cd engine && .venv/bin/pytest tests/test_api_recordings.py -v`
Expected: PASS

- [ ] **Step 8: Full regression + commit**

Run: `cd engine && .venv/bin/pytest -q`
Expected: all pass

```bash
git add engine/src/atlas_engine/db/models/portal.py engine/migrations/versions/ engine/src/atlas_engine/recording_service.py engine/src/atlas_engine/api.py engine/tests/test_recording_service.py engine/tests/test_api_recordings.py
git commit -m "feat: editable recording title/description and submit-to-developer"
```

---

### Task 5: ReviewPage editable fields + "submit to developer" button

**Files:**
- Modify: `frontend/src/pages/agent/ReviewPage.tsx`
- Modify: `frontend/src/lib/recordings.ts` (`RecordingDetail`, new `usePatchRecording`, `useSubmitRecording`)
- Create: `frontend/src/pages/agent/ReviewPage.test.tsx`

**Interfaces:**
- Consumes: `PATCH /recordings/{id}` and `POST /recordings/{id}/submit` from Task 4.
- Produces: `usePatchRecording(recordingId): UseMutationResult<void, ApiError, { title?: string; description?: string }>`; `useSubmitRecording(recordingId): UseMutationResult<{ request_id: string }, ApiError, { submittedBy: string }>`.

- [ ] **Step 1: Add `title`/`description` to `RecordingDetail` and the two hooks**

In `frontend/src/lib/recordings.ts`, add `title: string | null` and `description: string | null` to the `RecordingDetail` interface, and add:

```typescript
export function usePatchRecording(recordingId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { title?: string; description?: string }) =>
      apiFetch<{ ok: boolean }>(`/recordings/${recordingId}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['recording', recordingId] }),
  })
}

export function useSubmitRecording(recordingId: string | undefined) {
  return useMutation({
    mutationFn: (body: { submittedBy: string }) =>
      apiFetch<{ request_id: string }>(`/recordings/${recordingId}/submit`, {
        method: 'POST',
        body: JSON.stringify({ submitted_by: body.submittedBy }),
      }),
  })
}
```

`useQueryClient` is already imported in this file's neighbors (`recordingRequests.ts`) — add `useQueryClient` to `recordings.ts`'s existing `import { useMutation, useQuery } from '@tanstack/react-query'` line, making it `import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'`.

- [ ] **Step 2: Write the failing page test**

```tsx
// frontend/src/pages/agent/ReviewPage.test.tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../auth/AuthContext'
import { ReviewPage } from './ReviewPage'

const RECORDING = {
  id: 'rec-1', status: 'captured', role: 'demo', request_id: null,
  title: null, description: null, latest_authoring_run_id: null, codegen_script: null,
  pages: [{ page_index: 0, has_video: false, video_asset_id: null, duration_seconds: 10, steps: [] }],
}

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'agent-1', org_id: 'org-1', email: 'a@x.com', role: 'member', job_function: 'operations_agent' }))
    if (path === '/recordings/rec-1' && (init?.method ?? 'GET') === 'GET') return Promise.resolve(jsonResponse(RECORDING))
    if (path === '/recordings/rec-1/submit' && init?.method === 'POST') return Promise.resolve(jsonResponse({ request_id: 'req-9' }))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={['/agent/recordings/rec-1']}>
          <Routes>
            <Route path="/agent/recordings/:recordingId" element={<ReviewPage />} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  )
}

describe('ReviewPage submit-to-developer', () => {
  it('shows the submit button for an unlinked recording and calls submit on click', async () => {
    renderPage()
    const submitButton = await screen.findByRole('button', { name: /submit to developer/i })
    fireEvent.click(submitButton)
    await waitFor(() => expect(screen.getByText(/submitted/i)).toBeInTheDocument())
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/agent/ReviewPage.test.tsx`
Expected: FAIL — no "submit to developer" button exists yet.

- [ ] **Step 4: Implement the editable fields + submit button**

Modify `frontend/src/pages/agent/ReviewPage.tsx`: add the imports `useState` from `'react'`, `usePatchRecording`, `useSubmitRecording` from `'../../lib/recordings'`, `useAuth` from `'../../auth/AuthContext'`. Inside the component, after `const { data: recording, isLoading } = useRecordingDetail(recordingId)` add:

```tsx
  const { user } = useAuth()
  const patchRecording = usePatchRecording(recordingId)
  const submitRecording = useSubmitRecording(recordingId)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [submitted, setSubmitted] = useState(false)

  useEffect(() => {
    if (recording) {
      setTitle(recording.title ?? '')
      setDescription(recording.description ?? '')
    }
  }, [recording])
```

(add `useEffect` to the existing `react-router-dom`-adjacent React import line at the top of the file — the file currently has no React hook imports, so add `import { useEffect, useState } from 'react'` as a new top-level import).

Replace the early-return guard's placement is fine as-is (`if (isLoading || !recording) return ...` stays before these hooks — actually hooks must run unconditionally before any early return, so move the `useEffect`/`useState` calls above the `if (isLoading || !recording)` line, matching React's rules-of-hooks; `recording` will be `undefined` during loading, which `useEffect`'s `if (recording)` guard already handles).

Then, inside the JSX, right after the existing `<h1>Recording review</h1>` block's closing `</div>` (the header `flex items-start justify-between` block), add — only when `recording.request_id === null`:

```tsx
      {recording.request_id === null && (
        <div className="bg-atlas-card border border-atlas-border rounded-2xl p-5 flex flex-col gap-3 max-w-lg">
          <input
            placeholder="Title (e.g. Check eligibility)"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => patchRecording.mutate({ title })}
            className="px-3.5 py-2.5 rounded-[10px] border border-atlas-border outline-none focus:border-atlas-accent text-sm"
          />
          <textarea
            placeholder="Description (optional)"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            onBlur={() => patchRecording.mutate({ description })}
            className="px-3.5 py-2.5 rounded-[10px] border border-atlas-border outline-none focus:border-atlas-accent text-sm"
          />
          {submitted ? (
            <div className="text-[13px] text-atlas-primary font-medium">Submitted to the developer queue.</div>
          ) : (
            <button
              onClick={() => submitRecording.mutate({ submittedBy: user!.user_id }, { onSuccess: () => setSubmitted(true) })}
              disabled={submitRecording.isPending}
              className="bg-atlas-primary hover:bg-atlas-primaryHover text-white rounded-full py-2.5 text-[13.5px] font-medium disabled:opacity-60"
            >
              {submitRecording.isPending ? 'Submitting…' : 'Submit to developer'}
            </button>
          )}
        </div>
      )}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/agent/ReviewPage.test.tsx`
Expected: PASS

- [ ] **Step 6: Full frontend suite + commit**

Run: `cd frontend && npm test && npm run lint && npm run build`
Expected: PASS

```bash
git add frontend/src/pages/agent/ReviewPage.tsx frontend/src/pages/agent/ReviewPage.test.tsx frontend/src/lib/recordings.ts
git commit -m "feat: editable title/description and submit-to-developer button on ReviewPage"
```

---

### Task 6: Server-side role enforcement

**Files:**
- Create: `engine/src/atlas_engine/auth/deps.py`
- Modify: `engine/src/atlas_engine/api.py`
- Test: `engine/tests/test_api_auth_guards.py`

**Interfaces:**
- Produces: `CurrentUser` (dataclass: `id: uuid.UUID`, `org_id: uuid.UUID`, `role: str`, `job_function: str | None`); `get_current_user(atlas_session: str | None = Cookie(default=None)) -> CurrentUser`; `require_job_function(*allowed: str)` (FastAPI dependency factory).

- [ ] **Step 1: Write the failing dependency test**

```python
# engine/tests/test_api_auth_guards.py
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, User


def _make_user(*, job_function: str | None, role: str = "member") -> tuple[str, TestClient]:
    with session_scope() as session:
        org = Organization(name="Auth Guard Org", slug=f"auth-guard-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        user = User(
            org_id=org.id, email=f"{uuid.uuid4()}@example.com", password_hash=hash_password("pw"),
            role=role, job_function=job_function,
        )
        session.add(user)
        session.flush()
        email = user.email

    client = TestClient(app)
    login = client.post("/auth/login", json={"email": email, "password": "pw"})
    assert login.status_code == 200
    return login.json()["org_id"], client


def test_claim_request_requires_developer_job_function():
    _, agent_client = _make_user(job_function="operations_agent")
    response = agent_client.post(f"/recording-requests/{uuid.uuid4()}/claim", json={"developer_id": str(uuid.uuid4())})
    assert response.status_code == 403


def test_claim_request_allows_developer_job_function():
    org_id, dev_client = _make_user(job_function="developer")
    from atlas_engine.recording_service import create_recording_request

    request_id = create_recording_request(org_id, str(uuid.uuid4()), "https://portal.example.com/x", "Title")
    response = dev_client.post(f"/recording-requests/{request_id}/claim", json={"developer_id": str(uuid.uuid4())})
    assert response.status_code == 200


def test_claim_request_allows_admin_regardless_of_job_function():
    org_id, admin_client = _make_user(job_function="operations_agent", role="admin")
    from atlas_engine.recording_service import create_recording_request

    request_id = create_recording_request(org_id, str(uuid.uuid4()), "https://portal.example.com/x", "Title")
    response = admin_client.post(f"/recording-requests/{request_id}/claim", json={"developer_id": str(uuid.uuid4())})
    assert response.status_code == 200


def test_claim_request_401_without_a_session():
    client = TestClient(app)
    response = client.post(f"/recording-requests/{uuid.uuid4()}/claim", json={"developer_id": str(uuid.uuid4())})
    assert response.status_code == 401


def test_approve_heal_event_requires_developer_job_function():
    _, agent_client = _make_user(job_function="operations_agent")
    response = agent_client.post(f"/heal-events/{uuid.uuid4()}/approve")
    assert response.status_code == 403


def test_submit_recording_requires_operations_agent_job_function():
    _, dev_client = _make_user(job_function="developer")
    response = dev_client.post(f"/recordings/{uuid.uuid4()}/submit", json={"submitted_by": str(uuid.uuid4())})
    assert response.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_api_auth_guards.py -v`
Expected: FAIL — every guarded endpoint currently returns 200/404 with no auth check at all (no 403/401 anywhere).

- [ ] **Step 3: Implement `auth/deps.py`**

```python
# engine/src/atlas_engine/auth/deps.py
import uuid
from dataclasses import dataclass

from fastapi import Cookie, HTTPException

from atlas_engine.auth.session import verify_session_token
from atlas_engine.db import session_scope
from atlas_engine.db.models import User

SESSION_COOKIE_NAME = "atlas_session"


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    org_id: uuid.UUID
    role: str
    job_function: str | None


def get_current_user(atlas_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME)) -> CurrentUser:
    if atlas_session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = verify_session_token(atlas_session)
    if payload is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    with session_scope() as session:
        user = session.get(User, uuid.UUID(payload["user_id"]))
        if user is None:
            raise HTTPException(status_code=401, detail="User no longer exists")
        return CurrentUser(id=user.id, org_id=user.org_id, role=user.role, job_function=user.job_function)


def require_job_function(*allowed: str):
    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.job_function not in allowed and user.role not in ("owner", "admin"):
            raise HTTPException(status_code=403, detail=f"Requires job function: {', '.join(allowed)}")
        return user

    return dependency
```

Add `from fastapi import Depends` to this file's `fastapi` import (`from fastapi import Cookie, Depends, HTTPException`).

Note the `alias=SESSION_COOKIE_NAME` — this must resolve to the literal cookie name `"atlas_session"` that `api.py`'s existing `/auth/login` already sets via its own `SESSION_COOKIE_NAME = "atlas_session"` constant; both constants must have the same string value (they're intentionally duplicated rather than imported cross-module here, matching the codebase's existing pattern of small self-contained modules — a follow-on could consolidate them, out of scope for this task).

- [ ] **Step 4: Apply the guards in `api.py`**

Add the import `from atlas_engine.auth.deps import get_current_user, require_job_function` to `api.py`.

Change these endpoint signatures (add the `Depends(...)` parameter; the parameter can be unused — name it `_user` — except where noted):

```python
@app.post("/recording-requests/{request_id}/claim")
def claim_recording_request_endpoint(
    request_id: str, body: ClaimRequestBody, _user: CurrentUser = Depends(require_job_function("developer")),
) -> dict:
```

```python
@app.post("/recordings/{recording_id}/author", response_model=StartAuthoringRunResponse)
def author_from_recording_endpoint(
    recording_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> StartAuthoringRunResponse:
```

```python
@app.post("/authoring-runs/{authoring_run_id}/resume")
def resume_authoring_run_endpoint(
    authoring_run_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> dict:
```

```python
@app.post("/authoring-runs/{authoring_run_id}/stop")
def stop_authoring_run_endpoint(
    authoring_run_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> dict:
```

```python
@app.post("/heal-events/{heal_event_id}/approve", response_model=ApprovalResponse)
def approve_endpoint(
    heal_event_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> ApprovalResponse:
```

```python
@app.post("/heal-events/{heal_event_id}/reject", response_model=ApprovalResponse)
def reject_endpoint(
    heal_event_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> ApprovalResponse:
```

```python
@app.post("/recordings/{recording_id}/submit", response_model=SubmitRecordingResponse)
def submit_recording_endpoint(
    recording_id: str, body: SubmitRecordingBody,
    _user: CurrentUser = Depends(require_job_function("operations_agent")),
) -> SubmitRecordingResponse:
```

Add `from atlas_engine.auth.deps import CurrentUser, get_current_user, require_job_function` (i.e. include `CurrentUser` in the same import line, needed for the type annotations above).

Leave every `GET`/list endpoint (`/recordings`, `/recordings/{id}`, `/recording-requests`, `/heal-events`, `/authoring-runs/{id}`, extractions) unguarded — the spec scopes enforcement to state-changing endpoints only, matching the test suite above.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_api_auth_guards.py -v`
Expected: PASS

- [ ] **Step 6: Full regression + commit**

Run: `cd engine && .venv/bin/pytest -q`
Expected: all pass — this step matters most for this task, since it now guards six endpoints that other tests call directly (`test_api_recordings.py`'s submit test, any existing tests hitting claim/author/approve/reject/resume/stop without a session). If any pre-existing test now 401s, that test was calling a state-changing endpoint without going through `/auth/login` first — fix it by logging in a user with the right `job_function` at the top of that test (see the `_make_user`/`login` pattern above) rather than weakening the guard.

```bash
git add engine/src/atlas_engine/auth/deps.py engine/src/atlas_engine/api.py engine/tests/test_api_auth_guards.py
git commit -m "feat: enforce developer/operations_agent job_function on state-changing endpoints"
```

*(If Step 6 surfaces pre-existing tests that call a now-guarded endpoint anonymously, add a login step to each — this is expected fallout of adding real enforcement, not a sign the guard is wrong. Search first: `grep -rln "\.claim\|/claim\|/author\"\|/approve\|/reject\|/resume\|/stop\|/submit" engine/tests/` to find every test that might need updating.)*

---

### Task 7: Heal-event before/after screenshots (backend)

**Files:**
- Modify: `engine/src/atlas_engine/verify/heal.py`
- Modify: `engine/src/atlas_engine/api.py`
- Test: `engine/tests/test_verify_heal.py`, `engine/tests/test_api_heal_list.py`

**Interfaces:**
- Produces: `compute_trajectory_diff(old, new, outcomes=None) -> list[dict]` (each entry gains `before_screenshot: str | None`, `after_screenshot: str | None` when `outcomes` is given); `GET /heal-events/{heal_event_id}/screenshots/{step_index}/{when}` (`when` in `before`/`after`).

**Design note (do not touch `brain/loop.py`):** `run_step` already saves a screenshot per step under a `shot_dir` when one is given (used today only by the authoring brain phase), returning it on `StepOutcome.shot_after` (the post-action state for that step — `StepOutcome.shot_before` and `.shot_after` for a given step end up pointing at the *same* file on disk today, since `_save_shot` tags both by `step{index}` alone; this is existing behavior in a file we don't touch). To get two genuinely different images without changing `brain/loop.py`, "before" for a drifted step `i` is step `i-1`'s `shot_after` (the page state right after the prior step ran — i.e., right before step `i` ran), and "after" is step `i`'s own `shot_after`. `BrainLoop.run()` already returns a `BrainLoopResult` (a `list[StepOutcome]`), so this only requires passing `shot_dir` into the existing call and reading the already-returned outcomes — zero changes to `loop.py`/`evaluate.py`.

- [ ] **Step 1: Write the failing test for `compute_trajectory_diff` screenshots**

Add to `engine/tests/test_verify_heal.py` (check its existing imports first — it likely already imports `ReferenceTrajectory`, `TrajectoryStep`, `compute_trajectory_diff`):

```python
def test_compute_trajectory_diff_attaches_before_and_after_screenshots():
    from atlas_engine.brain.evaluate import StepOutcome

    old = ReferenceTrajectory(steps=(
        TrajectoryStep(index=0, kind="click", selector_candidates=[{"selector": "#old-a", "rank": 0}], value=None, expected_effect="none"),
        TrajectoryStep(index=1, kind="click", selector_candidates=[{"selector": "#old-b", "rank": 0}], value=None, expected_effect="none"),
    ))
    new = ReferenceTrajectory(steps=(
        TrajectoryStep(index=0, kind="click", selector_candidates=[{"selector": "#old-a", "rank": 0}], value=None, expected_effect="none"),
        TrajectoryStep(index=1, kind="click", selector_candidates=[{"selector": "#new-b", "rank": 0}], value=None, expected_effect="none"),
    ))
    outcomes = [
        StepOutcome(step_index=0, on_track=True, url_after="u", dom_after="d", resolved_selector="#old-a", shot_after="/tmp/step0.png"),
        StepOutcome(step_index=1, on_track=True, url_after="u", dom_after="d", resolved_selector="#new-b", shot_after="/tmp/step1.png"),
    ]

    diff = compute_trajectory_diff(old, new, outcomes=outcomes)

    assert len(diff) == 1
    assert diff[0]["step_index"] == 1
    assert diff[0]["before_screenshot"] == "/tmp/step0.png"
    assert diff[0]["after_screenshot"] == "/tmp/step1.png"


def test_compute_trajectory_diff_without_outcomes_omits_screenshots():
    old = ReferenceTrajectory(steps=(
        TrajectoryStep(index=0, kind="click", selector_candidates=[{"selector": "#old-a", "rank": 0}], value=None, expected_effect="none"),
    ))
    new = ReferenceTrajectory(steps=(
        TrajectoryStep(index=0, kind="click", selector_candidates=[{"selector": "#new-a", "rank": 0}], value=None, expected_effect="none"),
    ))
    diff = compute_trajectory_diff(old, new)
    assert diff[0]["before_screenshot"] is None
    assert diff[0]["after_screenshot"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_verify_heal.py -k screenshot -v`
Expected: FAIL — `TypeError: compute_trajectory_diff() got an unexpected keyword argument 'outcomes'`

- [ ] **Step 3: Implement the `outcomes` parameter**

In `engine/src/atlas_engine/verify/heal.py`, change `compute_trajectory_diff`:

```python
def compute_trajectory_diff(old: ReferenceTrajectory, new: ReferenceTrajectory, outcomes: list | None = None) -> list[dict]:
    shot_after_by_index: dict[int, str | None] = {}
    if outcomes is not None:
        shot_after_by_index = {o.step_index: o.shot_after for o in outcomes}

    diff = []
    for i in range(min(len(old.steps), len(new.steps))):
        old_selector = _primary_selector(old, i)
        new_selector = _primary_selector(new, i)
        if old_selector != new_selector:
            diff.append({
                "step_index": i,
                "field": "selector",
                "old_value": old_selector,
                "new_value": new_selector,
                "before_screenshot": shot_after_by_index.get(i - 1) if outcomes is not None else None,
                "after_screenshot": shot_after_by_index.get(i) if outcomes is not None else None,
            })
    return diff
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_verify_heal.py -k screenshot -v`
Expected: PASS

- [ ] **Step 5: Wire `shot_dir` into `heal_golden_script` and write its integration test**

Add to `engine/tests/test_verify_heal.py`:

```python
def test_heal_golden_script_records_screenshot_paths_when_a_step_drifts(fixture_portal_url):
    """Full integration: a real heal run against the fixture portal, with a
    golden script whose recorded selector for one step no longer matches, must
    produce a HealEvent whose diff_summary entries carry real screenshot files."""
    import json
    from pathlib import Path

    from atlas_engine.authoring_service import author_from_recording
    from atlas_engine.brain.serialize import trajectory_from_dict, trajectory_to_dict
    from atlas_engine.capture.events import RecordedAction
    from atlas_engine.config import settings
    from atlas_engine.db.models import GoldenScript, ReferenceTrajectory as ReferenceTrajectoryModel
    from atlas_engine.llm.client import FakeLlmClient
    from atlas_engine.recording_service import persist_recording

    with session_scope() as session:
        org = Organization(name="Heal Shot Org", slug=f"heal-shot-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    actions = [
        RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None,
                        start_url, f"{fixture_portal_url}/dashboard.html", "", "", timestamp_ms=1000),
    ]
    recording_id = persist_recording(org_id, start_url, actions)
    result = author_from_recording(recording_id, llm=FakeLlmClient(responses=[]))
    golden_script_id = uuid.UUID(result["golden_script_id"])

    # Break the recorded selector so the step drifts and the brain has to repair it.
    with session_scope() as session:
        traj_row = session.query(ReferenceTrajectoryModel).filter_by(recording_id=recording_id).one()
        traj_dict = json.loads(Path(traj_row.trajectory_blob_ref).read_text())
        traj_dict["steps"][0]["selector_candidates"] = [{"selector": "#does-not-exist", "rank": 0}]
        Path(traj_row.trajectory_blob_ref).write_text(json.dumps(traj_dict))

    fake_llm = FakeLlmClient(responses=[
        {"action": "replace_selector", "selector": "#login-btn", "reason": "recorded selector is stale"},
    ])
    heal_result = heal_golden_script(golden_script_id, llm=fake_llm)

    assert heal_result["status"] == "healed"
    with session_scope() as session:
        heal_event = session.get(HealEvent, uuid.UUID(heal_result["heal_event_id"]))
        entry = heal_event.diff_summary["changes"][0]
        assert entry["after_screenshot"] is not None
        assert Path(entry["after_screenshot"]).exists()
```

This test mirrors an existing heal integration test's setup if one exists in this file already — check the top of `test_verify_heal.py` for a similar `author_from_recording` + `heal_golden_script` pattern and reuse its exact fixture/action shape instead of inventing a new one, to avoid duplicating brittle setup.

Now implement. In `engine/src/atlas_engine/verify/heal.py`, inside `heal_golden_script`, right before the `with sync_playwright() as playwright:` block, add:

```python
    store = LocalRunStore(root=settings.ensure_run_root())
    shot_run_dir = store.new_run_dir(prefix="heal-shots")
    shot_dir = str(shot_run_dir.path)
```

Change the `BrainLoop(...).run(old_trajectory)` call to `BrainLoop(page, llm=llm, action_cache=cache, golden_script_id=golden_script_id).run(old_trajectory, shot_dir=shot_dir)`.

Change `diff = compute_trajectory_diff(old_trajectory, new_trajectory)` to `diff = compute_trajectory_diff(old_trajectory, new_trajectory, outcomes=result)` (`result` — the `BrainLoopResult` — is already a `list[StepOutcome]`, so this satisfies the new `outcomes` parameter directly).

Note `store`/`run_dir` already exist later in the function (`store = LocalRunStore(...)`, `run_dir = store.new_run_dir(prefix="goldens")`) — rename the new ones as `shot_run_dir`/`shot_dir` (as above) to avoid shadowing, and leave the later `store`/`run_dir` assignment for the golden script exactly as it is today (it's a second, independent `LocalRunStore` instantiation — harmless, matches the existing style of this function which already does this for other blob kinds).

- [ ] **Step 6: Run the integration test**

Run: `cd engine && .venv/bin/pytest tests/test_verify_heal.py -k screenshot -v`
Expected: PASS. If the `FakeLlmClient` responses shape doesn't match what `brain/repair.py::think` expects, check an existing passing heal/repair test in this repo for the exact JSON shape `FakeLlmClient` needs to return (e.g. `test_brain_repair.py`) and match it — do not guess at the shape or invent new repair-response fields, since that file is on the do-not-touch list and its contract is fixed.

- [ ] **Step 7: Add the screenshot-serving endpoint**

In `engine/src/atlas_engine/api.py`, add the import `list_heal_events` line already imports from `verify.heal` — no new import needed beyond what's already there (`HealEvent` needs importing from `atlas_engine.db.models` — add it to the existing `from atlas_engine.db.models import (...)` block).

```python
@app.get("/heal-events/{heal_event_id}/screenshots/{step_index}/{when}")
def get_heal_event_screenshot_endpoint(heal_event_id: str, step_index: int, when: str):
    if when not in ("before", "after"):
        raise HTTPException(status_code=404, detail="'when' must be 'before' or 'after'")

    with session_scope() as session:
        heal_event = session.get(HealEvent, uuid.UUID(heal_event_id))
        if heal_event is None:
            raise HTTPException(status_code=404, detail=f"No such heal event: {heal_event_id}")
        entry = next(
            (c for c in heal_event.diff_summary.get("changes", []) if c["step_index"] == step_index), None,
        )
        if entry is None:
            raise HTTPException(status_code=404, detail="No diff entry for this step index")
        screenshot_path = entry.get(f"{when}_screenshot")

    if not screenshot_path or not Path(screenshot_path).exists():
        raise HTTPException(status_code=404, detail="Screenshot not available")
    return FileResponse(screenshot_path, media_type="image/png")
```

- [ ] **Step 8: Write and run the endpoint test**

Add to `engine/tests/test_api_heal_list.py`:

```python
def test_get_heal_event_screenshot_404s_when_missing(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Heal Shot API Org", slug=f"heal-shot-api-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), fixture_portal_url.split("//")[1])
        script = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="unused", status="active")
        session.add(script)
        session.flush()
        event = HealEvent(org_id=org.id, golden_script_id=script.id, trigger="on_demand", status="pending_approval", diff_summary={"changes": []})
        session.add(event)
        session.flush()
        event_id = event.id

    client = TestClient(app)
    response = client.get(f"/heal-events/{event_id}/screenshots/0/before")
    assert response.status_code == 404
```

Run: `cd engine && .venv/bin/pytest tests/test_api_heal_list.py -v`
Expected: PASS

- [ ] **Step 9: Full regression + commit**

Run: `cd engine && .venv/bin/pytest -q`
Expected: all pass

```bash
git add engine/src/atlas_engine/verify/heal.py engine/src/atlas_engine/api.py engine/tests/test_verify_heal.py engine/tests/test_api_heal_list.py
git commit -m "feat: capture and serve before/after screenshots for heal events"
```

---

### Task 8: ApprovalsPage renders before/after screenshots

**Files:**
- Modify: `frontend/src/pages/ApprovalsPage.tsx`
- Modify: `frontend/src/lib/healEvents.ts` (`HealDiffEntry`, new `healEventScreenshotUrl`)
- Create: `frontend/src/pages/ApprovalsPage.test.tsx`

- [ ] **Step 1: Add the screenshot fields and URL helper**

In `frontend/src/lib/healEvents.ts`, add to `HealDiffEntry`:

```typescript
export interface HealDiffEntry {
  step_index: number
  field: string
  old_value: string
  new_value: string
  before_screenshot: string | null
  after_screenshot: string | null
}

export function healEventScreenshotUrl(healEventId: string, stepIndex: number, when: 'before' | 'after'): string {
  const base = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  return `${base}/heal-events/${healEventId}/screenshots/${stepIndex}/${when}`
}
```

- [ ] **Step 2: Write the failing test**

```tsx
// frontend/src/pages/ApprovalsPage.test.tsx
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../auth/AuthContext'
import { ApprovalsPage } from './ApprovalsPage'

const HEAL_EVENT = {
  id: 'heal-1', golden_script_id: 'gs-1', portal_domain: 'portal.example.com', trigger: 'on_demand',
  status: 'pending_approval', created_at: new Date().toISOString(),
  diff_summary: [{ step_index: 0, field: 'selector', old_value: '#a', new_value: '#b', before_screenshot: '/x/before.png', after_screenshot: '/x/after.png' }],
}

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'dev-1', org_id: 'org-1', email: 'd@x.com', role: 'member', job_function: 'developer' }))
    if (path.startsWith('/heal-events?')) return Promise.resolve(jsonResponse([HEAL_EVENT]))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider><ApprovalsPage /></AuthProvider>
    </QueryClientProvider>,
  )
}

describe('ApprovalsPage screenshots', () => {
  it('renders before/after images when the diff entry has them', async () => {
    renderPage()
    const beforeImg = await screen.findByAltText(/before/i)
    const afterImg = await screen.findByAltText(/after/i)
    expect(beforeImg).toHaveAttribute('src', expect.stringContaining('/heal-events/heal-1/screenshots/0/before'))
    expect(afterImg).toHaveAttribute('src', expect.stringContaining('/heal-events/heal-1/screenshots/0/after'))
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/ApprovalsPage.test.tsx`
Expected: FAIL — no `alt=/before/i` image exists yet.

- [ ] **Step 4: Implement the image rendering**

In `frontend/src/pages/ApprovalsPage.tsx`, add the import `healEventScreenshotUrl` to the existing `useApproveHealEvent, useHealEvents, useRejectHealEvent` import line from `'../lib/healEvents'`. Inside the `{(selected.diff_summary ?? []).map((entry) => (...))}` block, right before the existing `<div className="grid grid-cols-2 gap-[18px]">` (the old/new text pair), add:

```tsx
                <div key={`${entry.step_index}-shots`} className="grid grid-cols-2 gap-[18px]">
                  {entry.before_screenshot && (
                    <img
                      src={healEventScreenshotUrl(selected.id, entry.step_index, 'before')}
                      alt={`Step ${entry.step_index + 1} before`}
                      className="rounded-xl border border-atlas-border w-full"
                    />
                  )}
                  {entry.after_screenshot && (
                    <img
                      src={healEventScreenshotUrl(selected.id, entry.step_index, 'after')}
                      alt={`Step ${entry.step_index + 1} after`}
                      className="rounded-xl border border-atlas-border w-full"
                    />
                  )}
                </div>
```

(The existing `.map((entry) => (` callback currently returns a single top-level `<div key={entry.step_index} className="grid grid-cols-2 gap-[18px]">...</div>` for the text diff — wrap both this new screenshot block and the existing text block in a React Fragment with the original `key` moved to the fragment: `<Fragment key={entry.step_index}>{screenshotsBlock}{textBlock}</Fragment>`, importing `Fragment` from `'react'`.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/ApprovalsPage.test.tsx`
Expected: PASS

- [ ] **Step 6: Full frontend suite + commit**

Run: `cd frontend && npm test && npm run lint && npm run build`
Expected: PASS

```bash
git add frontend/src/pages/ApprovalsPage.tsx frontend/src/pages/ApprovalsPage.test.tsx frontend/src/lib/healEvents.ts
git commit -m "feat: render before/after screenshots on the heal approvals page"
```

---

### Task 9: LLM cost/token instrumentation and summary endpoint

**Files:**
- Modify: `engine/src/atlas_engine/llm/client.py` (`AzureOpenAiClient`)
- Modify: `engine/src/atlas_engine/llm/factory.py` (`create_llm_client`)
- Create: `engine/src/atlas_engine/llm/usage.py`
- Modify: `engine/src/atlas_engine/api.py`, `engine/src/atlas_engine/cli.py`
- Test: `engine/tests/test_llm_client.py`, `engine/tests/test_llm_usage.py`, `engine/tests/test_api_llm_usage.py`

**No migration needed** — `UsageLedgerEntry` (`engine/src/atlas_engine/db/models/execution.py`) already exists with exactly the needed columns (`org_id`, `user_id`, `run_id`, `recording_id`, `provider`, `model`, `tokens_in`, `tokens_out`, `cost_usd`, `created_at`) and is currently written to and read from nowhere in the codebase.

**Interfaces:**
- Produces: `AzureOpenAiClient(..., on_usage: Callable[[dict], None] | None = None)`; `create_llm_client(*, usage_context: tuple[str, str] | None = None) -> LlmClient | None` (context is `(org_id, recording_id)`); `record_llm_usage(org_id: str, recording_id: str | None, *, model: str, input_tokens: int | None, output_tokens: int | None) -> None`; `list_llm_usage_summary(org_id: str) -> dict` (`{"total_calls", "total_input_tokens", "total_output_tokens", "total_cost_usd", "by_day": [{"date", "calls", "cost_usd"}, ...]}`).

- [ ] **Step 1: Write the failing `on_usage` callback test**

Add to `engine/tests/test_llm_client.py` (check its existing imports/mocking pattern for `AzureOpenAiClient` first — it likely already mocks `httpx.post`):

```python
def test_azure_client_invokes_on_usage_with_token_counts(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 45},
            }

    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResponse())

    client = AzureOpenAiClient(
        endpoint="https://x", api_key="k", deployment="gpt-4o",
        on_usage=lambda usage: captured.update(usage),
    )
    client.complete("prompt", json_mode=True)

    assert captured == {"model": "gpt-4o", "input_tokens": 120, "output_tokens": 45}


def test_azure_client_on_usage_never_raises_on_malformed_usage(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}  # no "usage" key at all

    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResponse())

    def blow_up(usage):
        raise RuntimeError("should never propagate")

    client = AzureOpenAiClient(endpoint="https://x", api_key="k", deployment="gpt-4o", on_usage=blow_up)
    result = client.complete("prompt", json_mode=True)
    assert result == {}


def test_azure_client_without_on_usage_is_unaffected(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResponse())

    client = AzureOpenAiClient(endpoint="https://x", api_key="k", deployment="gpt-4o")
    assert client.complete("prompt", json_mode=True) == {"ok": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_llm_client.py -k on_usage -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'on_usage'`

- [ ] **Step 3: Implement the `on_usage` callback**

In `engine/src/atlas_engine/llm/client.py`, add `from typing import Any, Callable, Protocol` (add `Callable` to the existing `typing` import). Change `AzureOpenAiClient.__init__`:

```python
    def __init__(
        self, endpoint: str, api_key: str, deployment: str, api_version: str = "2024-10-21",
        on_usage: "Callable[[dict], None] | None" = None,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.deployment = deployment
        self.api_version = api_version
        self.on_usage = on_usage
```

In `complete()`, change:

```python
        response = httpx.post(url, headers={"api-key": self.api_key}, json=payload, timeout=60.0)
        response.raise_for_status()
        data = response.json()

        if self.on_usage is not None:
            try:
                usage = data.get("usage") or {}
                self.on_usage({
                    "model": target_model,
                    "input_tokens": usage.get("prompt_tokens"),
                    "output_tokens": usage.get("completion_tokens"),
                })
            except Exception:
                pass

        raw_text = data["choices"][0]["message"]["content"]
```

(replacing the previous `response = httpx.post(...)` / `response.raise_for_status()` / `raw_text = response.json()[...]` three lines — the rest of the method, from `cleaned = re.sub(...)` onward, is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_llm_client.py -v`
Expected: PASS (all tests in the file, not just the new ones — confirms the refactor from two `response.json()` calls to one didn't change existing behavior)

- [ ] **Step 5: Write the failing `llm/usage.py` tests**

```python
# engine/tests/test_llm_usage.py
import uuid

from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, UsageLedgerEntry
from atlas_engine.llm.usage import list_llm_usage_summary, record_llm_usage


def test_record_llm_usage_computes_cost_from_the_pricing_table():
    with session_scope() as session:
        org = Organization(name="Usage Org", slug=f"usage-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    record_llm_usage(org_id, None, model="gpt-4o", input_tokens=1000, output_tokens=1000)

    with session_scope() as session:
        entry = session.query(UsageLedgerEntry).filter_by(org_id=uuid.UUID(org_id)).one()
        assert entry.tokens_in == 1000
        assert entry.tokens_out == 1000
        assert entry.provider == "azure_openai"
        assert float(entry.cost_usd) > 0


def test_record_llm_usage_defaults_unknown_model_cost_to_zero():
    with session_scope() as session:
        org = Organization(name="Usage Unknown Model Org", slug=f"usage-unknown-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    record_llm_usage(org_id, None, model="some-future-model", input_tokens=500, output_tokens=500)

    with session_scope() as session:
        entry = session.query(UsageLedgerEntry).filter_by(org_id=uuid.UUID(org_id)).one()
        assert float(entry.cost_usd) == 0.0


def test_list_llm_usage_summary_aggregates_totals_and_by_day():
    with session_scope() as session:
        org = Organization(name="Usage Summary Org", slug=f"usage-summary-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    record_llm_usage(org_id, None, model="gpt-4o", input_tokens=100, output_tokens=50)
    record_llm_usage(org_id, None, model="gpt-4o", input_tokens=200, output_tokens=100)

    summary = list_llm_usage_summary(org_id)
    assert summary["total_calls"] == 2
    assert summary["total_input_tokens"] == 300
    assert summary["total_output_tokens"] == 150
    assert summary["total_cost_usd"] > 0
    assert len(summary["by_day"]) == 1
    assert summary["by_day"][0]["calls"] == 2
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_llm_usage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atlas_engine.llm.usage'`

- [ ] **Step 7: Implement `llm/usage.py`**

```python
# engine/src/atlas_engine/llm/usage.py
import uuid
from collections import defaultdict
from decimal import Decimal

from atlas_engine.db import session_scope
from atlas_engine.db.models import UsageLedgerEntry

# $ per 1,000 tokens (input, output). Unrecognized models cost $0 rather than
# erroring — this table only needs entries for models actually configured via
# ATLAS_AZURE_OPENAI_MODEL / AZURE_OPENAI_MODEL; extend it as new models are used.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-5": (0.005, 0.015),
}


def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    input_rate, output_rate = PRICING.get(model, (0.0, 0.0))
    return Decimal(str(input_tokens / 1000 * input_rate + output_tokens / 1000 * output_rate))


def record_llm_usage(
    org_id: str, recording_id: str | None, *, model: str, input_tokens: int | None, output_tokens: int | None,
) -> None:
    tokens_in = input_tokens or 0
    tokens_out = output_tokens or 0
    with session_scope() as session:
        session.add(UsageLedgerEntry(
            org_id=uuid.UUID(org_id),
            recording_id=uuid.UUID(recording_id) if recording_id else None,
            provider="azure_openai",
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=_cost_usd(model, tokens_in, tokens_out),
        ))


def list_llm_usage_summary(org_id: str) -> dict:
    with session_scope() as session:
        entries = session.query(UsageLedgerEntry).filter_by(org_id=uuid.UUID(org_id)).all()

        by_day: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost_usd": 0.0})
        total_input = total_output = 0
        total_cost = Decimal("0")
        for entry in entries:
            total_input += entry.tokens_in
            total_output += entry.tokens_out
            total_cost += entry.cost_usd
            day_key = entry.created_at.date().isoformat()
            by_day[day_key]["calls"] += 1
            by_day[day_key]["cost_usd"] += float(entry.cost_usd)

        return {
            "total_calls": len(entries),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cost_usd": float(total_cost),
            "by_day": [{"date": day, **stats} for day, stats in sorted(by_day.items())],
        }
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_llm_usage.py -v`
Expected: PASS

- [ ] **Step 9: Wire `usage_context` through the factory and the four call sites**

In `engine/src/atlas_engine/llm/factory.py`:

```python
def create_llm_client(*, usage_context: tuple[str, str | None] | None = None) -> LlmClient | None:
    """Reads Azure OpenAI config from the environment; returns None (no
    repair capability — an off-track step stays off-track) if unconfigured.
    Supports both ATLAS_AZURE_OPENAI_* and standard AZURE_OPENAI_* env vars
    for seamless parity with the previous codebase. `usage_context`, when
    given as (org_id, recording_id), records a UsageLedgerEntry per LLM call
    via the client's on_usage hook — purely additive telemetry, invisible to
    every caller inside brain/*."""
    endpoint = os.environ.get("ATLAS_AZURE_OPENAI_ENDPOINT") or os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("ATLAS_AZURE_OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")
    model = (
        os.environ.get("ATLAS_AZURE_OPENAI_MODEL")
        or os.environ.get("AZURE_OPENAI_MODEL")
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME")
    )

    if not (endpoint and api_key and model):
        return None

    on_usage = None
    if usage_context is not None:
        org_id, recording_id = usage_context
        from atlas_engine.llm.usage import record_llm_usage

        def on_usage(usage: dict) -> None:
            record_llm_usage(
                org_id, recording_id, model=usage["model"],
                input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
            )

    return AzureOpenAiClient(endpoint=endpoint, api_key=api_key, deployment=model, on_usage=on_usage)
```

In `engine/src/atlas_engine/api.py`:

```python
@app.post("/golden-scripts/{golden_script_id}/heal", response_model=HealResponse)
def heal_endpoint(golden_script_id: str) -> HealResponse:
    with session_scope() as session:
        golden = session.get(GoldenScript, uuid.UUID(golden_script_id))
        usage_context = (str(golden.org_id), str(golden.created_from_recording_id)) if golden else None
    llm = create_llm_client(usage_context=usage_context)
    result = heal_golden_script(uuid.UUID(golden_script_id), llm=llm)
    return HealResponse(**result)
```

```python
@app.post("/recordings/{recording_id}/author", response_model=StartAuthoringRunResponse)
def author_from_recording_endpoint(
    recording_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> StartAuthoringRunResponse:
    with session_scope() as session:
        recording = session.get(Recording, uuid.UUID(recording_id))
        usage_context = (str(recording.org_id), recording_id) if recording else None
    llm = create_llm_client(usage_context=usage_context)
    try:
        authoring_run_id = start_authoring_run(uuid.UUID(recording_id), llm=llm)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return StartAuthoringRunResponse(authoring_run_id=str(authoring_run_id))
```

`GoldenScript` is already imported in `api.py`; `Recording` is already imported too — no new imports needed for these two endpoints.

In `engine/src/atlas_engine/cli.py`, add `GoldenScript, Recording` to the existing `from atlas_engine.db.models import User` line (making it `from atlas_engine.db.models import GoldenScript, Recording, User`), and change:

```python
@main.command("heal")
@click.argument("golden_script_id")
def heal(golden_script_id: str) -> None:
    """Re-validate a golden script against its live portal and, if a step
    has drifted, propose a repaired version pending approval."""
    with session_scope() as session:
        golden = session.get(GoldenScript, uuid.UUID(golden_script_id))
        usage_context = (str(golden.org_id), str(golden.created_from_recording_id)) if golden else None
    llm = create_llm_client(usage_context=usage_context)
    result = heal_golden_script(uuid.UUID(golden_script_id), llm=llm)
```

```python
@main.command("author")
@click.argument("recording_id")
def author(recording_id: str) -> None:
    """Author a golden script from an already-persisted recording —
    the decoupled counterpart to author-from-actions, for recordings that
    already exist as Recording rows (via `atlas record`, by anyone)."""
    with session_scope() as session:
        recording = session.get(Recording, uuid.UUID(recording_id))
        usage_context = (str(recording.org_id), recording_id) if recording else None
    llm = create_llm_client(usage_context=usage_context)
    result = author_from_recording(uuid.UUID(recording_id), llm=llm)
```

(the rest of both CLI command bodies is unchanged — only the `llm = create_llm_client()` line becomes the block above.)

- [ ] **Step 10: Add the summary endpoint and its test**

In `engine/src/atlas_engine/api.py`, add the import `from atlas_engine.llm.usage import list_llm_usage_summary`, and:

```python
@app.get("/llm-usage/summary")
def llm_usage_summary_endpoint(
    org_id: str, _user: CurrentUser = Depends(require_job_function("developer")),
) -> dict:
    return list_llm_usage_summary(org_id)
```

```python
# engine/tests/test_api_llm_usage.py
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, User
from atlas_engine.llm.usage import record_llm_usage


def test_llm_usage_summary_requires_developer_job_function():
    with session_scope() as session:
        org = Organization(name="Usage API Org", slug=f"usage-api-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent = User(org_id=org.id, email=f"{uuid.uuid4()}@example.com", password_hash=hash_password("pw"), job_function="operations_agent")
        session.add(agent)
        session.flush()
        email = agent.email

    client = TestClient(app)
    client.post("/auth/login", json={"email": email, "password": "pw"})
    response = client.get(f"/llm-usage/summary?org_id={org_id}")
    assert response.status_code == 403


def test_llm_usage_summary_returns_totals_for_a_developer():
    with session_scope() as session:
        org = Organization(name="Usage API Dev Org", slug=f"usage-api-dev-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        dev = User(org_id=org.id, email=f"{uuid.uuid4()}@example.com", password_hash=hash_password("pw"), job_function="developer")
        session.add(dev)
        session.flush()
        email = dev.email

    record_llm_usage(org_id, None, model="gpt-4o", input_tokens=10, output_tokens=10)

    client = TestClient(app)
    client.post("/auth/login", json={"email": email, "password": "pw"})
    response = client.get(f"/llm-usage/summary?org_id={org_id}")
    assert response.status_code == 200
    assert response.json()["total_calls"] == 1
```

- [ ] **Step 11: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_api_llm_usage.py -v`
Expected: PASS

- [ ] **Step 12: Full regression + commit**

Run: `cd engine && .venv/bin/pytest -q`
Expected: all pass

```bash
git add engine/src/atlas_engine/llm/client.py engine/src/atlas_engine/llm/factory.py engine/src/atlas_engine/llm/usage.py engine/src/atlas_engine/api.py engine/src/atlas_engine/cli.py engine/tests/test_llm_client.py engine/tests/test_llm_usage.py engine/tests/test_api_llm_usage.py
git commit -m "feat: instrument LLM calls with token/cost tracking via UsageLedgerEntry"
```

---

### Task 10: Cost dashboard page

**Files:**
- Create: `frontend/src/lib/llmUsage.ts`
- Create: `frontend/src/pages/dev/CostDashboardPage.tsx`
- Create: `frontend/src/pages/dev/CostDashboardPage.test.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/layout/AppLayout.tsx`

**Interfaces:**
- Consumes: `GET /llm-usage/summary?org_id=` from Task 9.

- [ ] **Step 1: Write the failing hook test**

```tsx
// frontend/src/lib/llmUsage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { useLlmUsageSummary } from './llmUsage'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/llm-usage/summary?org_id=org-1') {
      return Promise.resolve({
        ok: true, status: 200,
        json: async () => ({ total_calls: 3, total_input_tokens: 100, total_output_tokens: 50, total_cost_usd: 0.12, by_day: [{ date: '2026-08-14', calls: 3, cost_usd: 0.12 }] }),
      })
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

describe('useLlmUsageSummary', () => {
  it('fetches the summary for an org', async () => {
    const { result } = renderHook(() => useLlmUsageSummary('org-1'), { wrapper })
    await waitFor(() => expect(result.current.data?.total_calls).toBe(3))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/llmUsage.test.tsx`
Expected: FAIL — `Cannot find module './llmUsage'`

- [ ] **Step 3: Implement `lib/llmUsage.ts`**

```typescript
// frontend/src/lib/llmUsage.ts
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface LlmUsageDay {
  date: string
  calls: number
  cost_usd: number
}

export interface LlmUsageSummary {
  total_calls: number
  total_input_tokens: number
  total_output_tokens: number
  total_cost_usd: number
  by_day: LlmUsageDay[]
}

export function useLlmUsageSummary(orgId: string) {
  return useQuery({
    queryKey: ['llm-usage-summary', orgId],
    queryFn: () => apiFetch<LlmUsageSummary>(`/llm-usage/summary?org_id=${orgId}`),
  })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/llmUsage.test.tsx`
Expected: PASS

- [ ] **Step 5: Write the failing page test**

```tsx
// frontend/src/pages/dev/CostDashboardPage.test.tsx
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../auth/AuthContext'
import { CostDashboardPage } from './CostDashboardPage'

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'dev-1', org_id: 'org-1', email: 'd@x.com', role: 'member', job_function: 'developer' }))
    if (path === '/llm-usage/summary?org_id=org-1') {
      return Promise.resolve(jsonResponse({ total_calls: 3, total_input_tokens: 100, total_output_tokens: 50, total_cost_usd: 0.12, by_day: [{ date: '2026-08-14', calls: 3, cost_usd: 0.12 }] }))
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider><CostDashboardPage /></AuthProvider>
    </QueryClientProvider>,
  )
}

describe('CostDashboardPage', () => {
  it('renders totals and the per-day breakdown', async () => {
    renderPage()
    expect(await screen.findByText('3')).toBeInTheDocument()
    expect(screen.getByText('$0.12')).toBeInTheDocument()
    expect(screen.getByText('2026-08-14')).toBeInTheDocument()
  })
})
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/dev/CostDashboardPage.test.tsx`
Expected: FAIL — `Cannot find module './CostDashboardPage'`

- [ ] **Step 7: Implement `CostDashboardPage`**

```tsx
// frontend/src/pages/dev/CostDashboardPage.tsx
import { useAuth } from '../../auth/AuthContext'
import { useLlmUsageSummary } from '../../lib/llmUsage'

export function CostDashboardPage() {
  const { user } = useAuth()
  const { data, isLoading } = useLlmUsageSummary(user!.org_id)

  if (isLoading || !data) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  const maxCost = Math.max(...data.by_day.map((d) => d.cost_usd), 0.0001)

  return (
    <div className="p-9 pb-16 flex flex-col gap-7 max-w-[1180px]">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-display text-[30px] font-semibold tracking-tight m-0">Cost dashboard</h1>
        <div className="text-sm text-atlas-body">LLM spend across authoring and healing runs.</div>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <div className="bg-atlas-card border border-atlas-border rounded-2xl p-5 flex flex-col gap-1.5">
          <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Calls</div>
          <div className="text-2xl font-semibold tracking-tight">{data.total_calls}</div>
        </div>
        <div className="bg-atlas-card border border-atlas-border rounded-2xl p-5 flex flex-col gap-1.5">
          <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Input tokens</div>
          <div className="text-2xl font-semibold tracking-tight">{data.total_input_tokens.toLocaleString()}</div>
        </div>
        <div className="bg-atlas-card border border-atlas-border rounded-2xl p-5 flex flex-col gap-1.5">
          <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Output tokens</div>
          <div className="text-2xl font-semibold tracking-tight">{data.total_output_tokens.toLocaleString()}</div>
        </div>
        <div className="bg-atlas-card border border-atlas-border rounded-2xl p-5 flex flex-col gap-1.5">
          <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Total spend</div>
          <div className="text-2xl font-semibold tracking-tight">${data.total_cost_usd.toFixed(2)}</div>
        </div>
      </div>

      <div className="bg-atlas-card border border-atlas-border rounded-2xl p-6 flex flex-col gap-3">
        <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Spend by day</div>
        {data.by_day.map((day) => (
          <div key={day.date} className="flex items-center gap-3">
            <div className="text-[12.5px] text-atlas-body w-24 flex-none">{day.date}</div>
            <div className="flex-1 h-5 bg-atlas-bg rounded-full overflow-hidden">
              <div
                className="h-full bg-atlas-primary rounded-full"
                style={{ width: `${(day.cost_usd / maxCost) * 100}%` }}
              />
            </div>
            <div className="text-[12.5px] text-atlas-body w-16 flex-none text-right">${day.cost_usd.toFixed(2)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/dev/CostDashboardPage.test.tsx`
Expected: PASS

- [ ] **Step 9: Wire the route and nav entry**

In `frontend/src/App.tsx`, import `CostDashboardPage` and add `<Route path="/dev/costs" element={<CostDashboardPage />} />`.

In `frontend/src/layout/AppLayout.tsx`, add `{ to: '/dev/costs', label: 'Cost dashboard' }` to `NAV.dev`.

- [ ] **Step 10: Full frontend suite + commit**

Run: `cd frontend && npm test && npm run lint && npm run build`
Expected: PASS

```bash
git add frontend/src/lib/llmUsage.ts frontend/src/pages/dev/CostDashboardPage.tsx frontend/src/pages/dev/CostDashboardPage.test.tsx frontend/src/App.tsx frontend/src/layout/AppLayout.tsx
git commit -m "feat: add LLM cost dashboard for developers"
```

---

### Final step: full-system regression

- [ ] Run: `cd engine && .venv/bin/pytest -q` — expect all pass (197+ pre-existing plus ~35 new).
- [ ] Run: `cd frontend && npm test && npm run lint && npm run build` — expect all pass.
- [ ] Manually smoke-test with `/run` (or the project's existing dev-server workflow): log in as an operations-agent user, confirm `/agent/library` lists recordings and `/agent/recordings/:id` still shows video+steps only; log in as a developer, confirm `/dev/requests` lists and claims requests, `/dev/costs` renders, and `ApprovalsPage` shows screenshots on a real heal event if one exists.
- [ ] Confirm `git diff main --stat` touches zero files under `engine/src/atlas_engine/brain/`, `engine/src/atlas_engine/authoring/replay.py`, or `engine/src/atlas_engine/codegen/emit.py` — the hard constraint from the spec.
