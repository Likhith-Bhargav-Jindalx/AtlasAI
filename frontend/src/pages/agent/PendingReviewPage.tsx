import { useNavigate, useParams } from 'react-router-dom'
import {
  pendingRecordingVideoUrl, useDiscardPendingRecording, usePendingRecordingDetail, useUploadPendingRecording,
} from '../../lib/pendingRecordings'
import { formatDuration } from '../../lib/format'

export function PendingReviewPage() {
  const { pendingId } = useParams<{ pendingId: string }>()
  const navigate = useNavigate()
  const { data: pending, isLoading } = usePendingRecordingDetail(pendingId)
  const upload = useUploadPendingRecording(pendingId)
  const discard = useDiscardPendingRecording(pendingId)

  if (isLoading || !pending) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  const mainPage = pending.pages[0]

  async function handleUpload() {
    const result = await upload.mutateAsync()
    navigate(`/agent/recordings/${result.recording_id}`)
  }

  async function handleDiscard() {
    await discard.mutateAsync()
    navigate('/agent/pending')
  }

  return (
    <div className="p-9 pb-16 flex flex-col gap-6 max-w-[1280px]">
      <div className="flex items-start justify-between gap-6">
        <div className="flex flex-col gap-2">
          <h1 className="font-display text-[28px] font-semibold tracking-tight m-0">Review before upload</h1>
          <div className="text-[13.5px] text-atlas-body">
            {pending.start_url} · {pending.action_count} action{pending.action_count === 1 ? '' : 's'}
            {mainPage ? ` · ${formatDuration(mainPage.duration_seconds)}` : ''}
          </div>
          <div className="text-[12.5px] text-atlas-body">
            Only on this machine until uploaded — nobody else can see this recording yet.
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleDiscard}
            disabled={upload.isPending || discard.isPending}
            className="px-5 py-2.5 rounded-full border border-atlas-border text-[13.5px] font-medium disabled:opacity-60"
          >
            {discard.isPending ? 'Discarding…' : 'Discard'}
          </button>
          <button
            onClick={handleUpload}
            disabled={upload.isPending || discard.isPending}
            className="px-5 py-2.5 rounded-full bg-atlas-primary hover:bg-atlas-primaryHover text-white text-[13.5px] font-medium disabled:opacity-60"
          >
            {upload.isPending ? 'Uploading…' : 'Upload'}
          </button>
        </div>
      </div>

      {upload.isError && (
        <div className="text-[13px] text-atlas-danger">{(upload.error as Error).message}</div>
      )}

      {mainPage?.has_video && (
        <video src={pendingRecordingVideoUrl(pending.id, 0)} controls className="rounded-2xl overflow-hidden border border-atlas-border bg-black aspect-video w-full max-w-[720px]" />
      )}

      <div className="flex flex-col gap-2">
        {mainPage?.steps.map((step) => (
          <div key={step.order_index} className="text-[13.5px] text-atlas-ink py-1.5 border-b border-atlas-rowBorder">
            {step.caption}
          </div>
        ))}
      </div>
    </div>
  )
}
