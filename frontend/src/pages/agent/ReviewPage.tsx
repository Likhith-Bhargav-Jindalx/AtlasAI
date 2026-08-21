import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useRecordingDetail, usePatchRecording, useSubmitRecording, type UpdateRecordingDetailsBody } from '../../lib/recordings'
import { useAuth } from '../../auth/AuthContext'
import { VideoStepPlayer } from '../../components/VideoStepPlayer'
import { StatusPill } from '../../components/StatusPill'
import { RecordingFormSectionB } from '../../components/RecordingFormSectionB'
import { formatDuration } from '../../lib/format'

export function ReviewPage() {
  const { recordingId } = useParams<{ recordingId: string }>()
  const navigate = useNavigate()
  const { data: recording, isLoading } = useRecordingDetail(recordingId)

  const { user } = useAuth()
  const patchRecording = usePatchRecording(recordingId)
  const sectionBPatch = usePatchRecording(recordingId)
  const submitRecording = useSubmitRecording(recordingId)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [sectionBSaved, setSectionBSaved] = useState(false)

  useEffect(() => {
    if (recording) {
      setTitle(recording.title ?? '')
      setDescription(recording.description ?? '')
    }
  }, [recording])

  if (isLoading || !recording) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  const totalActions = recording.pages.reduce((sum, page) => sum + page.steps.length, 0)
  const mainPage = recording.pages[0]

  async function handleSubmit() {
    if (!recording) return
    const titleDirty = title !== (recording.title ?? '')
    const descriptionDirty = description !== (recording.description ?? '')
    if (titleDirty || descriptionDirty) {
      try {
        await patchRecording.mutateAsync({ title, description })
      } catch {
        // Surfaced via patchRecording.isError below; don't submit a stale title.
        return
      }
    }
    submitRecording.mutate({ submittedBy: user!.user_id }, { onSuccess: () => setSubmitted(true) })
  }

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
          {(patchRecording.isError || submitRecording.isError) && (
            <div className="text-[13px] text-atlas-diffOldText">
              {((patchRecording.error ?? submitRecording.error) as Error).message}
            </div>
          )}

          {submitted ? (
            <div className="text-[13px] text-atlas-primary font-medium">Submitted to the developer queue.</div>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={submitRecording.isPending || patchRecording.isPending}
              className="bg-atlas-primary hover:bg-atlas-primaryHover text-white rounded-full py-2.5 text-[13.5px] font-medium disabled:opacity-60"
            >
              {submitRecording.isPending || patchRecording.isPending ? 'Submitting…' : 'Submit to developer'}
            </button>
          )}
        </div>
      )}

      {recording.lob && (
        <div className="flex flex-col gap-2 max-w-lg">
          <RecordingFormSectionB
            lob={recording.lob as 'AR' | 'EV'}
            action={recording.action}
            initial={recording}
            onSubmit={(values: UpdateRecordingDetailsBody) => sectionBPatch.mutate(values, { onSuccess: () => setSectionBSaved(true) })}
            isSubmitting={sectionBPatch.isPending}
            error={sectionBPatch.isError ? (sectionBPatch.error as Error).message : undefined}
          />
          {sectionBSaved && !sectionBPatch.isError && (
            <div className="text-[13px] text-atlas-primary font-medium">Context saved.</div>
          )}
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
    </div>
  )
}
