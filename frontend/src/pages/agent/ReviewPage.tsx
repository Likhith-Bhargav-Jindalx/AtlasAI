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

  const totalActions = recording.pages.reduce((sum, page) => sum + page.steps.length, 0)
  const mainPage = recording.pages[0]

  return (
    <div className="p-9 pb-16 flex flex-col gap-6 max-w-[1280px]">
      <div className="flex items-start justify-between gap-6">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2.5">
            <StatusPill status={recording.status} />
            <span className="text-[12.5px] text-atlas-muted">
              {formatDuration(mainPage?.duration_seconds ?? null)} · {totalActions} action{totalActions === 1 ? '' : 's'}
              {recording.pages.length > 1 ? ` · ${recording.pages.length} tabs` : ''}
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
    </div>
  )
}
