# Authoring Pipeline Frontend UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the developer's `AuthoringPage` real observability into the authoring pipeline: a phase tracker, a scrolling event log, Resume/Stop controls, and a code viewer for the generated golden script — replacing the old single-click-and-wait "Generate golden script" flow with live polling of the async pipeline built in the two prior plans.

**Architecture:** One rewritten data-access module (`frontend/src/lib/authoring.ts`) exposing the new async endpoints via `react-query`, three new small presentational components, and a rewired `AuthoringPage`. No new backend work — this plan only consumes `POST /recordings/{id}/author`, `GET /authoring-runs/{id}`, `POST /authoring-runs/{id}/resume`, `POST /authoring-runs/{id}/stop`, all already merged.

**Tech Stack:** React 18, TypeScript, `@tanstack/react-query` v5, Tailwind CSS, `lucide-react` icons, Vitest + `@testing-library/react` for tests, matching every existing convention in `frontend/src/`.

**Spec:** `docs/superpowers/specs/2026-08-13-authoring-pipeline-rebuild-design.md`

## Global Constraints

- Polling only, no new push infrastructure — `react-query`'s `refetchInterval`, matching the spec's confirmed choice. Poll every 1.5s while the run is active; stop polling once the run reaches a terminal status (`completed`, `failed`, `cancelled`).
- Resume button shown only when `status === "waiting_for_developer"`; Stop button shown whenever the run is active (`running` or `waiting_for_developer`).
- No new backend endpoints in this plan. `frontend/src/lib/recordings.ts`, `frontend/src/components/VideoStepPlayer.tsx`, and everything else already working are untouched except where explicitly listed.
- Known, accepted limitation (already flagged during the API plan's final review, not this plan's job to fix): there is no way to look up an existing recording's authoring run after a page reload — `authoringRunId` lives only in this page's React state for the duration of one session. Reloading mid-run means losing the ability to see/resume/stop that run from the UI (the run itself keeps running server-side). Do not attempt to add a lookup endpoint in this plan.
- Match the existing test depth in this codebase: pure/exported logic gets unit tests (Vitest, no rendering) — see `frontend/src/components/VideoStepPlayer.test.tsx` for the exact pattern. Full component rendering tests (`@testing-library/react`, see `frontend/src/App.test.tsx`) are reserved for one basic smoke test, not exhaustive UI testing.

---

### Task 1: `frontend/src/lib/authoring.ts` — types and hooks for the async pipeline

**Files:**
- Modify: `frontend/src/lib/authoring.ts`
- Create: `frontend/src/lib/authoring.test.ts`

**Interfaces:**
- Consumes: `apiFetch` from `frontend/src/lib/api.ts` (existing).
- Produces: `AuthoringRunEvent`, `CertificationRun`, `GoldenScriptSummary`, `AuthoringRunDetail` interfaces (matching `engine/src/atlas_engine/api.py`'s `AuthoringRunEventResponse`/`CertificationRunResponse`/`GoldenScriptSummaryResponse`/`AuthoringRunDetailResponse` field-for-field). `isTerminalStatus(status: string): boolean`. `useStartAuthoringRun(recordingId: string)`, `useAuthoringRun(authoringRunId: string | undefined)`, `useResumeAuthoringRun(authoringRunId: string | undefined)`, `useStopAuthoringRun(authoringRunId: string | undefined)`.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/lib/authoring.test.ts
import { describe, expect, it } from 'vitest'
import { isTerminalStatus } from './authoring'

describe('isTerminalStatus', () => {
  it('treats completed, failed, and cancelled as terminal', () => {
    expect(isTerminalStatus('completed')).toBe(true)
    expect(isTerminalStatus('failed')).toBe(true)
    expect(isTerminalStatus('cancelled')).toBe(true)
  })

  it('treats running and waiting_for_developer as non-terminal', () => {
    expect(isTerminalStatus('running')).toBe(false)
    expect(isTerminalStatus('waiting_for_developer')).toBe(false)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npm run test -- authoring.test.ts`
Expected: FAIL — `isTerminalStatus` is not exported yet (the current file has no such function).

- [ ] **Step 3: Rewrite the implementation**

Replace the entire contents of `frontend/src/lib/authoring.ts`:

```typescript
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface AuthoringRunEvent {
  phase: string
  step_index: number | null
  level: string
  message: string
}

export interface CertificationRun {
  run_number: number
  status: string
  failure_evidence_blob_ref: string | null
}

export interface GoldenScriptSummary {
  id: string
  version: number
  status: string
  script_text: string | null
}

export interface AuthoringRunDetail {
  id: string
  status: string
  current_phase: string
  repair_attempt: number
  events: AuthoringRunEvent[]
  golden_script: GoldenScriptSummary | null
  certification_runs: CertificationRun[]
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])

export function isTerminalStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(status)
}

export function useStartAuthoringRun(recordingId: string) {
  return useMutation({
    mutationFn: () =>
      apiFetch<{ authoring_run_id: string }>(`/recordings/${recordingId}/author`, { method: 'POST' }),
  })
}

export function useAuthoringRun(authoringRunId: string | undefined) {
  return useQuery({
    queryKey: ['authoring-run', authoringRunId],
    queryFn: () => apiFetch<AuthoringRunDetail>(`/authoring-runs/${authoringRunId}`),
    enabled: !!authoringRunId,
    refetchInterval: (query) => {
      const data = query.state.data
      if (data && isTerminalStatus(data.status)) return false
      return 1500
    },
  })
}

export function useResumeAuthoringRun(authoringRunId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<{ ok: boolean }>(`/authoring-runs/${authoringRunId}/resume`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['authoring-run', authoringRunId] }),
  })
}

export function useStopAuthoringRun(authoringRunId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<{ ok: boolean }>(`/authoring-runs/${authoringRunId}/stop`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['authoring-run', authoringRunId] }),
  })
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npm run test -- authoring.test.ts`
Expected: PASS (2/2)

- [ ] **Step 5: Confirm the build now fails where callers need updating**

Run: `cd frontend && npm run build`
Expected: FAIL — `AuthoringPage.tsx` still imports the removed `useAuthorRecording`/`AuthorResult`. This is expected; Task 5 fixes the only caller.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/authoring.ts frontend/src/lib/authoring.test.ts
git commit -m "feat: authoring.ts exposes the async pipeline (start/poll/resume/stop)"
```

---

### Task 2: `StatusPill` additions and `AuthoringPhaseTracker` component

**Files:**
- Modify: `frontend/src/components/StatusPill.tsx`
- Create: `frontend/src/components/AuthoringPhaseTracker.tsx`
- Create: `frontend/src/components/AuthoringPhaseTracker.test.tsx`

**Interfaces:**
- Consumes: nothing new from Task 1 directly (works off plain `currentPhase`/`status`/`repairAttempt` props, kept decoupled from the data-fetching hook so it's trivially testable).
- Produces: `PHASE_ORDER: string[]` (exported constant: `["replay", "extract", "brain", "golden", "certifying", "done"]`), `phaseIndex(phase: string): number` (exported pure function), `AuthoringPhaseTracker` component with props `{ currentPhase: string; status: string; repairAttempt: number }`.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/AuthoringPhaseTracker.test.tsx
import { describe, expect, it } from 'vitest'
import { PHASE_ORDER, phaseIndex } from './AuthoringPhaseTracker'

describe('phaseIndex', () => {
  it('returns the position of each phase in PHASE_ORDER', () => {
    expect(phaseIndex('replay')).toBe(0)
    expect(phaseIndex('brain')).toBe(2)
    expect(phaseIndex('done')).toBe(PHASE_ORDER.length - 1)
  })

  it('returns -1 for an unknown phase', () => {
    expect(phaseIndex('not-a-real-phase')).toBe(-1)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npm run test -- AuthoringPhaseTracker.test.tsx`
Expected: FAIL — the module doesn't exist yet.

- [ ] **Step 3: Add the new StatusPill colors**

In `frontend/src/components/StatusPill.tsx`, add these entries to the existing `PILL_COLORS` map (the map already has `failed`/`cancelled`/`certified`, which apply here unchanged — only add the ones genuinely missing):

```typescript
  running: { bg: '#fdf6e7', fg: '#8a6a1f' },
  waiting_for_developer: { bg: '#fdf6e7', fg: '#8a6a1f' },
  completed: { bg: '#144e48', fg: '#ffffff' },
  partially_certified: { bg: '#fdf6e7', fg: '#8a6a1f' },
```

- [ ] **Step 4: Write the component**

```tsx
// frontend/src/components/AuthoringPhaseTracker.tsx
export const PHASE_ORDER = ['replay', 'extract', 'brain', 'golden', 'certifying', 'done']

export function phaseIndex(phase: string): number {
  return PHASE_ORDER.indexOf(phase)
}

const PHASE_LABELS: Record<string, string> = {
  replay: 'Replay',
  extract: 'Extract',
  brain: 'Brain',
  golden: 'Golden',
  certifying: 'Certifying',
  done: 'Done',
}

interface Props {
  currentPhase: string
  status: string
  repairAttempt: number
}

export function AuthoringPhaseTracker({ currentPhase, status, repairAttempt }: Props) {
  const activeIndex = phaseIndex(currentPhase)
  const failed = status === 'failed'
  const cancelled = status === 'cancelled'

  return (
    <div className="flex items-center gap-2">
      {PHASE_ORDER.map((phase, i) => {
        const isPast = i < activeIndex
        const isActive = i === activeIndex && !failed && !cancelled
        const isErrored = i === activeIndex && (failed || cancelled)
        return (
          <div key={phase} className="flex items-center gap-2">
            <div
              className={`px-2.5 py-1 rounded-full text-[11.5px] font-medium ${
                isErrored
                  ? 'bg-atlas-diffOldText/10 text-atlas-diffOldText'
                  : isActive
                    ? 'bg-atlas-primary text-white'
                    : isPast
                      ? 'bg-atlas-accentSubtle text-atlas-primary'
                      : 'bg-atlas-bg text-atlas-muted'
              }`}
            >
              {PHASE_LABELS[phase]}
            </div>
            {i < PHASE_ORDER.length - 1 && <div className="w-4 h-px bg-atlas-border" />}
          </div>
        )
      })}
      {repairAttempt > 0 && status !== 'completed' && (
        <span className="text-[11.5px] text-atlas-muted ml-1">repair attempt {repairAttempt}/2</span>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend && npm run test -- AuthoringPhaseTracker.test.tsx`
Expected: PASS (2/2)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/StatusPill.tsx frontend/src/components/AuthoringPhaseTracker.tsx frontend/src/components/AuthoringPhaseTracker.test.tsx
git commit -m "feat: add AuthoringPhaseTracker and new StatusPill colors"
```

---

### Task 3: `AuthoringEventLog` component

**Files:**
- Create: `frontend/src/components/AuthoringEventLog.tsx`
- Create: `frontend/src/components/AuthoringEventLog.test.tsx`

**Interfaces:**
- Consumes: `AuthoringRunEvent` from Task 1's `frontend/src/lib/authoring.ts`.
- Produces: `levelColorClass(level: string): string` (exported pure function). `AuthoringEventLog` component with props `{ events: AuthoringRunEvent[] }`.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/AuthoringEventLog.test.tsx
import { describe, expect, it } from 'vitest'
import { levelColorClass } from './AuthoringEventLog'

describe('levelColorClass', () => {
  it('returns a distinct class per level', () => {
    expect(levelColorClass('info')).toBe('text-atlas-body')
    expect(levelColorClass('warning')).toBe('text-atlas-muted')
    expect(levelColorClass('error')).toBe('text-atlas-diffOldText')
  })

  it('falls back to the info color for an unknown level', () => {
    expect(levelColorClass('not-a-real-level')).toBe('text-atlas-body')
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npm run test -- AuthoringEventLog.test.tsx`
Expected: FAIL — the module doesn't exist yet.

- [ ] **Step 3: Write the component**

```tsx
// frontend/src/components/AuthoringEventLog.tsx
import type { AuthoringRunEvent } from '../lib/authoring'

const LEVEL_COLORS: Record<string, string> = {
  info: 'text-atlas-body',
  warning: 'text-atlas-muted',
  error: 'text-atlas-diffOldText',
}

export function levelColorClass(level: string): string {
  return LEVEL_COLORS[level] ?? LEVEL_COLORS.info
}

interface Props {
  events: AuthoringRunEvent[]
}

export function AuthoringEventLog({ events }: Props) {
  if (events.length === 0) {
    return <div className="text-[13px] text-atlas-muted">No events yet.</div>
  }

  return (
    <div className="bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
      <div className="px-[18px] py-3.5 border-b border-atlas-border text-[13.5px] font-semibold">Log</div>
      <div className="max-h-[280px] overflow-auto flex flex-col">
        {events.map((event, i) => (
          <div
            key={i}
            className={`px-[18px] py-2.5 border-b border-atlas-rowBorder text-[12.5px] font-mono ${levelColorClass(event.level)}`}
          >
            <span className="text-atlas-muted">[{event.phase}]</span>{' '}
            {event.step_index !== null && <span className="text-atlas-muted">step {event.step_index}: </span>}
            {event.message}
          </div>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npm run test -- AuthoringEventLog.test.tsx`
Expected: PASS (2/2)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/AuthoringEventLog.tsx frontend/src/components/AuthoringEventLog.test.tsx
git commit -m "feat: add AuthoringEventLog component"
```

---

### Task 4: `GoldenScriptViewer` component

**Files:**
- Create: `frontend/src/components/GoldenScriptViewer.tsx`

**Interfaces:**
- Consumes: `GoldenScriptSummary` from Task 1's `frontend/src/lib/authoring.ts`.
- Produces: `GoldenScriptViewer` component with props `{ goldenScript: GoldenScriptSummary | null }`.

This component has no non-trivial pure logic to extract (it's a straightforward conditional render), so per the plan's Global Constraints on matching this codebase's existing test depth, it gets no dedicated test file — consistent with how simple presentational pieces elsewhere in this codebase (e.g. `StatusPill`) have no test file of their own either.

- [ ] **Step 1: Write the component**

```tsx
// frontend/src/components/GoldenScriptViewer.tsx
import { StatusPill } from './StatusPill'
import type { GoldenScriptSummary } from '../lib/authoring'

interface Props {
  goldenScript: GoldenScriptSummary | null
}

export function GoldenScriptViewer({ goldenScript }: Props) {
  if (goldenScript === null) {
    return <div className="text-[13px] text-atlas-muted">No golden script yet.</div>
  }

  return (
    <div className="bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
      <div className="px-[18px] py-3.5 border-b border-atlas-border flex items-center justify-between">
        <div className="text-[13.5px] font-semibold">Golden script v{goldenScript.version}</div>
        <StatusPill status={goldenScript.status} />
      </div>
      {goldenScript.script_text !== null ? (
        <pre className="p-[18px] text-[12.5px] font-mono overflow-auto max-h-[420px] whitespace-pre">
          {goldenScript.script_text}
        </pre>
      ) : (
        <div className="p-[18px] text-[13px] text-atlas-muted">Script file not found on disk.</div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Confirm it compiles**

Run: `cd frontend && npm run build`
Expected: this component alone introduces no new build errors (the overall build may still fail per Task 1 Step 5's expected state, since `AuthoringPage.tsx` isn't fixed until Task 5 — confirm no *new* errors reference `GoldenScriptViewer.tsx` itself).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/GoldenScriptViewer.tsx
git commit -m "feat: add GoldenScriptViewer component"
```

---

### Task 5: Rewire `AuthoringPage`, manual verification, and finish branch

**Files:**
- Modify: `frontend/src/pages/dev/AuthoringPage.tsx`

**Interfaces:**
- Consumes: everything from Tasks 1-4.

- [ ] **Step 1: Rewrite the page**

Replace the entire contents of `frontend/src/pages/dev/AuthoringPage.tsx`:

```tsx
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useRecordingDetail } from '../../lib/recordings'
import {
  useAuthoringRun,
  useResumeAuthoringRun,
  useStartAuthoringRun,
  useStopAuthoringRun,
} from '../../lib/authoring'
import { VideoStepPlayer } from '../../components/VideoStepPlayer'
import { StatusPill } from '../../components/StatusPill'
import { AuthoringPhaseTracker } from '../../components/AuthoringPhaseTracker'
import { AuthoringEventLog } from '../../components/AuthoringEventLog'
import { GoldenScriptViewer } from '../../components/GoldenScriptViewer'
import { formatDuration } from '../../lib/format'

export function AuthoringPage() {
  const { recordingId } = useParams<{ recordingId: string }>()
  const navigate = useNavigate()
  const { data: recording, isLoading } = useRecordingDetail(recordingId)

  const [authoringRunId, setAuthoringRunId] = useState<string | undefined>(undefined)
  const startRun = useStartAuthoringRun(recordingId!)
  const authoringRun = useAuthoringRun(authoringRunId)
  const resumeRun = useResumeAuthoringRun(authoringRunId)
  const stopRun = useStopAuthoringRun(authoringRunId)

  if (isLoading || !recording) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  const totalActions = recording.pages.reduce((sum, page) => sum + page.steps.length, 0)
  const mainPage = recording.pages[0]
  const run = authoringRun.data
  const isActive = run !== undefined && (run.status === 'running' || run.status === 'waiting_for_developer')

  function handleStart() {
    startRun.mutate(undefined, {
      onSuccess: (data) => setAuthoringRunId(data.authoring_run_id),
    })
  }

  return (
    <div className="flex flex-col h-screen box-border">
      <div className="flex-none px-7 py-[18px] border-b border-atlas-border bg-atlas-card flex items-center justify-between gap-5">
        <div className="flex items-center gap-3.5 min-w-0">
          <button
            onClick={() => navigate('/dev')}
            className="w-[30px] h-[30px] rounded-full border border-atlas-border flex items-center justify-center hover:bg-atlas-bg flex-none"
          >
            <ArrowLeft size={15} className="text-atlas-body" />
          </button>
          <div className="flex flex-col gap-0.5 min-w-0">
            <div className="text-[15px] font-semibold tracking-tight">Authoring workspace</div>
            <div className="text-xs text-atlas-muted">
              {formatDuration(mainPage?.duration_seconds ?? null)} · {totalActions} actions
              {recording.pages.length > 1 ? ` · ${recording.pages.length} tabs` : ''}
            </div>
          </div>
          {run && <StatusPill status={run.status} />}
        </div>
        <div className="flex items-center gap-2.5 flex-none">
          {run?.status === 'waiting_for_developer' && (
            <button
              onClick={() => resumeRun.mutate()}
              disabled={resumeRun.isPending}
              className="px-[18px] py-2.5 rounded-full border border-atlas-border hover:bg-atlas-bg text-[13px] font-medium disabled:opacity-60"
            >
              {resumeRun.isPending ? 'Resuming…' : 'Resume'}
            </button>
          )}
          {isActive && (
            <button
              onClick={() => stopRun.mutate()}
              disabled={stopRun.isPending}
              className="px-[18px] py-2.5 rounded-full border border-atlas-border hover:bg-atlas-bg text-[13px] font-medium text-atlas-diffOldText disabled:opacity-60"
            >
              {stopRun.isPending ? 'Stopping…' : 'Stop'}
            </button>
          )}
          {!isActive && (
            <button
              onClick={handleStart}
              disabled={startRun.isPending}
              className="px-[18px] py-2.5 rounded-full bg-atlas-primary hover:bg-atlas-primaryHover text-white text-[13px] font-medium disabled:opacity-60"
            >
              {startRun.isPending ? 'Starting…' : run ? 'Regenerate golden script' : 'Generate golden script'}
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-auto p-7 flex flex-col gap-6">
        {run && (
          <div className="flex flex-col gap-4">
            <AuthoringPhaseTracker
              currentPhase={run.current_phase}
              status={run.status}
              repairAttempt={run.repair_attempt}
            />
            <AuthoringEventLog events={run.events} />
            <GoldenScriptViewer goldenScript={run.golden_script} />
          </div>
        )}

        {recording.pages.map((page) => (
          <div key={page.page_index} className="flex flex-col gap-3">
            {recording.pages.length > 1 && (
              <div className="text-[12.5px] font-medium text-atlas-body">
                {page.page_index === 0 ? 'Main window' : `Tab ${page.page_index + 1}`}
              </div>
            )}
            <VideoStepPlayer
              recordingId={recording.id}
              pageIndex={page.page_index}
              hasVideo={page.has_video}
              steps={page.steps}
              durationSeconds={page.duration_seconds}
            />
          </div>
        ))}

        {startRun.isError && (
          <div className="text-[13px] text-atlas-diffOldText">{(startRun.error as Error).message}</div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Run the build**

Run: `cd frontend && npm run build`
Expected: clean build, no type errors (this is the first time since Task 1 that every caller of the removed `useAuthorRecording`/`AuthorResult` has been fixed).

- [ ] **Step 3: Run the frontend test suite**

Run: `cd frontend && npm run test`
Expected: all tests pass (the existing 3 files plus this plan's new `authoring.test.ts`, `AuthoringPhaseTracker.test.tsx`, `AuthoringEventLog.test.tsx`).

- [ ] **Step 4: Manual verification**

Restart both dev servers (backend needs to be running for real API calls; frontend needs the new build):
- Backend: from `engine/`, `.venv/bin/uvicorn atlas_engine.api:app --reload --port 8000` (or confirm an existing instance is already running and picks up changes via `--reload`).
- Frontend: from `frontend/`, `npm run dev`.

In a browser, navigate to an existing recording's authoring page (or record a fresh one against the fixture portal or eCW) and click "Generate golden script". Confirm:
- The phase tracker appears and advances through replay → brain → golden → certifying → done as the run progresses (poll every ~1.5s, visible via the Network tab if desired).
- The event log fills in with real messages as steps complete.
- Once the golden phase completes, the code viewer shows the actual generated Python script text.
- Once certifying completes, the final status (`certified`/`partially_certified`) shows via the `StatusPill`.
- The Stop button is visible and clickable while the run is active, and clicking it transitions the run to `cancelled`.
- If you can reproduce a drift scenario (e.g. against the fixture portal with drift mode, which requires a throwaway script since there's no UI trigger for it — or skip this specific sub-case if impractical to set up manually and rely on the backend's own already-verified real human-interaction test from the orchestration plan instead), confirm the Resume button appears when `status` becomes `waiting_for_developer` and clicking it un-pauses the run.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/dev/AuthoringPage.tsx
git commit -m "feat: AuthoringPage shows live phase tracker, event log, and golden script"
```

- [ ] **Step 6: Finish the branch**

**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — run the full frontend build/test (and the backend suite too, to confirm nothing else regressed), present the merge/PR/keep menu, act on the answer.
