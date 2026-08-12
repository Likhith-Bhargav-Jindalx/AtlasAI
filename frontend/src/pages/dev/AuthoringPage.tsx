import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useRecordingDetail } from '../../lib/recordings'
import { useAuthorRecording } from '../../lib/authoring'
import { VideoStepPlayer } from '../../components/VideoStepPlayer'
import { StatusPill } from '../../components/StatusPill'
import { formatDuration } from '../../lib/format'

export function AuthoringPage() {
  const { recordingId } = useParams<{ recordingId: string }>()
  const navigate = useNavigate()
  const { data: recording, isLoading } = useRecordingDetail(recordingId)
  const authorRecording = useAuthorRecording(recordingId!)

  if (isLoading || !recording) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

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
            <div className="text-xs text-atlas-muted">{formatDuration(recording.duration_seconds)} · {recording.steps.length} actions</div>
          </div>
          {authorRecording.data && <StatusPill status={authorRecording.data.status} />}
        </div>
        <button
          onClick={() => authorRecording.mutate()}
          disabled={authorRecording.isPending}
          className="px-[18px] py-2.5 rounded-full bg-atlas-primary hover:bg-atlas-primaryHover text-white text-[13px] font-medium disabled:opacity-60"
        >
          {authorRecording.isPending ? 'Generating…' : authorRecording.data ? 'Regenerate golden script' : 'Generate golden script'}
        </button>
      </div>

      <div className="flex-1 overflow-auto p-7">
        <VideoStepPlayer
          recordingId={recording.id}
          hasVideo={recording.has_video}
          steps={recording.steps}
          durationSeconds={recording.duration_seconds}
        />
        {authorRecording.data && (
          <div className="mt-5 text-[13px] text-atlas-body">
            Golden script <span className="font-medium text-atlas-ink">{authorRecording.data.golden_script_id}</span> — {authorRecording.data.status}
          </div>
        )}
        {authorRecording.isError && (
          <div className="mt-5 text-[13px] text-atlas-diffOldText">{(authorRecording.error as Error).message}</div>
        )}
      </div>
    </div>
  )
}
