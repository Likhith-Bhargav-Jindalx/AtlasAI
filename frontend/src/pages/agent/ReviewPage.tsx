import { useNavigate, useParams } from 'react-router-dom'
import { useRecordingDetail } from '../../lib/recordings'
import { VideoStepPlayer } from '../../components/VideoStepPlayer'
import { StatusPill } from '../../components/StatusPill'
import { formatDuration } from '../../lib/format'

export function ReviewPage() {
  const { recordingId } = useParams<{ recordingId: string }>()
  const navigate = useNavigate()
  const { data: recording, isLoading } = useRecordingDetail(recordingId)

  if (isLoading || !recording) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  return (
    <div className="p-9 pb-16 flex flex-col gap-6 max-w-[1280px]">
      <div className="flex items-start justify-between gap-6">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2.5">
            <StatusPill status={recording.status} />
            <span className="text-[12.5px] text-atlas-muted">
              {formatDuration(recording.duration_seconds)} · {recording.steps.length} action{recording.steps.length === 1 ? '' : 's'}
            </span>
          </div>
          <h1 className="font-display text-[28px] font-semibold tracking-tight m-0">Recording review</h1>
          <div className="text-[13.5px] text-atlas-body">
            {recording.role === 'demo' ? 'Demo recording' : 'Authoring recording'}
          </div>
        </div>
        <button
          onClick={() => navigate('/agent')}
          className="px-5 py-2.5 rounded-full bg-atlas-primary hover:bg-atlas-primaryHover text-white text-[13.5px] font-medium"
        >
          Done
        </button>
      </div>

      <VideoStepPlayer
        recordingId={recording.id}
        hasVideo={recording.has_video}
        steps={recording.steps}
        durationSeconds={recording.duration_seconds}
      />
    </div>
  )
}
