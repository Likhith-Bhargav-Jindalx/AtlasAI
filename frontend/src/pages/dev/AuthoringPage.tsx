import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useRecordingDetail } from '../../lib/recordings'
import {
  isTerminalStatus,
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
import { PageExtractionsList } from '../../components/PageExtractionsList'
import { useRecordingExtractions } from '../../lib/extractions'
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
  const extractions = useRecordingExtractions(recordingId)

  if (isLoading || !recording) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  const totalActions = recording.pages.reduce((sum, page) => sum + page.steps.length, 0)
  const mainPage = recording.pages[0]
  const run = authoringRun.data
  const isActive = run !== undefined && !isTerminalStatus(run.status)

  // A run exists server-side from the moment the start mutation succeeds, but
  // the first GET /authoring-runs/{id} needs a round trip to prove it. Between
  // those two moments `run` is still undefined, so without this guard the
  // primary button would flip back to an enabled "Generate golden script" and a
  // second click would launch a second real pipeline we can neither see nor stop.
  const hasRun = authoringRunId !== undefined
  const awaitingFirstRunData = hasRun && !authoringRun.isError && (authoringRun.isLoading || run === undefined)
  const primaryDisabled = startRun.isPending || awaitingFirstRunData
  const primaryLabel =
    startRun.isPending || awaitingFirstRunData
      ? 'Starting…'
      : run
        ? 'Regenerate golden script'
        : 'Generate golden script'

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
              disabled={primaryDisabled}
              className="px-[18px] py-2.5 rounded-full bg-atlas-primary hover:bg-atlas-primaryHover text-white text-[13px] font-medium disabled:opacity-60"
            >
              {primaryLabel}
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-auto p-7 flex flex-col gap-6">
        {authoringRun.isError && (
          <div className="bg-atlas-card border border-atlas-border rounded-2xl px-[18px] py-3.5 text-[13px] text-atlas-diffOldText">
            Could not load authoring run status: {(authoringRun.error as Error).message}
          </div>
        )}

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

        {extractions.data && extractions.data.length > 0 && (
          <PageExtractionsList extractions={extractions.data} />
        )}

        {startRun.isError && (
          <div className="text-[13px] text-atlas-diffOldText">{(startRun.error as Error).message}</div>
        )}
        {resumeRun.isError && (
          <div className="text-[13px] text-atlas-diffOldText">
            Resume failed: {(resumeRun.error as Error).message}
          </div>
        )}
        {stopRun.isError && (
          <div className="text-[13px] text-atlas-diffOldText">Stop failed: {(stopRun.error as Error).message}</div>
        )}
      </div>
    </div>
  )
}
