import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import {
  pendingRecordingVideoUrl, useDiscardPendingRecording, usePendingRecordingDetail, useUploadPendingRecording,
} from '../../lib/pendingRecordings'
import { formatDuration } from '../../lib/format'
import { VideoStepPlayer } from '../../components/VideoStepPlayer'

export function PendingReviewPage() {
  const { pendingId } = useParams<{ pendingId: string }>()
  const navigate = useNavigate()
  const { data: pending, isLoading } = usePendingRecordingDetail(pendingId)
  const upload = useUploadPendingRecording(pendingId)
  const discard = useDiscardPendingRecording(pendingId)
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false)

  if (isLoading || !pending) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  const mainPage = pending.pages[0]
  const totalActions = pending.pages.reduce((sum, page) => sum + page.steps.length, 0)

  async function handleUpload() {
    const result = await upload.mutateAsync()
    navigate(`/agent/recordings/${result.recording_id}`)
  }

  async function handleDiscard() {
    setShowDiscardConfirm(false)
    await discard.mutateAsync()
    navigate('/agent/pending')
  }

  return (
    <div className="p-9 pb-16 flex flex-col gap-6 max-w-[1280px]">
      <div className="flex items-start justify-between gap-6">
        <div className="flex flex-col gap-2">
          <h1 className="font-display text-[28px] font-semibold tracking-tight m-0">Review before upload</h1>
          <div className="text-[13.5px] text-atlas-body">
            {pending.start_url} · {totalActions} action{totalActions === 1 ? '' : 's'}
            {mainPage ? ` · ${formatDuration(mainPage.duration_seconds)}` : ''}
            {pending.pages.length > 1 ? ` · ${pending.pages.length} tabs` : ''}
          </div>
          <div className="text-[12.5px] text-atlas-body">
            Only on this machine until uploaded — nobody else can see this recording yet.
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowDiscardConfirm(true)}
            disabled={upload.isPending || discard.isPending}
            className="px-5 py-2.5 rounded-full border border-atlas-border text-[13.5px] font-medium disabled:opacity-60"
          >
            {discard.isPending ? 'Discarding…' : 'Discard'}
          </button>
          <button
            onClick={handleUpload}
            disabled={upload.isPending || discard.isPending}
            className="flex items-center gap-2 px-5 py-2.5 rounded-full bg-atlas-primary hover:bg-atlas-primaryHover text-white text-[13.5px] font-medium disabled:opacity-60"
          >
            {upload.isPending && <Loader2 size={14} className="animate-spin" />}
            {upload.isPending ? 'Uploading…' : 'Upload'}
          </button>
        </div>
      </div>

      {showDiscardConfirm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-6">
          <div className="bg-atlas-card rounded-2xl w-full max-w-sm p-6 flex flex-col gap-4">
            <div className="font-display text-lg font-semibold tracking-tight">Discard this recording?</div>
            <div className="text-[13.5px] text-atlas-body">
              This deletes the local video and actions for good — it was never uploaded, so there's nothing to undo.
            </div>
            <div className="flex items-center justify-end gap-3 pt-1">
              <button
                onClick={() => setShowDiscardConfirm(false)}
                className="px-4 py-2 rounded-full border border-atlas-border text-[13.5px] font-medium"
              >
                Cancel
              </button>
              <button
                onClick={handleDiscard}
                className="px-4 py-2 rounded-full bg-atlas-danger hover:bg-atlas-dangerHover text-white text-[13.5px] font-medium"
              >
                Discard
              </button>
            </div>
          </div>
        </div>
      )}

      {upload.isError && (
        <div className="text-[13px] text-atlas-danger">{(upload.error as Error).message}</div>
      )}

      {pending.pages.map((page) => (
        <div key={page.page_index} className="flex flex-col gap-3">
          {pending.pages.length > 1 && (
            <div className="text-[12.5px] font-medium text-atlas-body">
              {page.page_index === 0 ? 'Main window' : `Tab ${page.page_index + 1}`}
            </div>
          )}
          <VideoStepPlayer
            recordingId={pending.id}
            pageIndex={page.page_index}
            hasVideo={page.has_video}
            steps={page.steps}
            durationSeconds={page.duration_seconds}
            videoUrl={pendingRecordingVideoUrl(pending.id, page.page_index)}
          />
        </div>
      ))}
    </div>
  )
}
