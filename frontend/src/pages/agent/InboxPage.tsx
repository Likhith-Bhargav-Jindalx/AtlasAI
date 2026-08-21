import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Inbox } from 'lucide-react'
import { useAuth } from '../../auth/AuthContext'
import { useCreateRecordingRequest, useRecordingRequests } from '../../lib/recordingRequests'
import { StatusPill } from '../../components/StatusPill'
import { RecordingFormSectionA, type SectionAValues } from '../../components/RecordingFormSectionA'

export function InboxPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const orgId = user!.org_id
  const { data: openRequests } = useRecordingRequests(orgId, 'open')
  const { data: submittedRequests } = useRecordingRequests(orgId, 'submitted')
  const createRequest = useCreateRecordingRequest()

  const [showForm, setShowForm] = useState(false)

  const requests = [...(openRequests ?? []), ...(submittedRequests ?? [])]

  async function handleCreate(values: SectionAValues) {
    const result = await createRequest.mutateAsync({
      org_id: orgId,
      requested_by: user!.user_id,
      target_url: values.targetUrl,
      title: values.workflowName || values.targetUrl,
      lob: values.lob || undefined,
      action: values.action || undefined,
      client_scope: values.client ? [values.client] : undefined,
      ehr: values.ehr || undefined,
      specific_to: values.specificTo || undefined,
    })
    navigate(`/agent/record?requestId=${result.request_id}&url=${encodeURIComponent(values.targetUrl)}`)
  }

  function startRecording(request: { id: string; target_url: string }) {
    navigate(`/agent/record?requestId=${request.id}&url=${encodeURIComponent(request.target_url)}`)
  }

  return (
    <div className="p-9 pb-16 flex flex-col gap-7 max-w-[1180px]">
      <div className="flex items-end justify-between gap-6">
        <div className="flex flex-col gap-1.5">
          <h1 className="font-display text-[30px] font-semibold tracking-tight m-0">Request inbox</h1>
          <div className="text-sm text-atlas-body">Open recording requests. Record the flow once — we handle the rest.</div>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-2 bg-atlas-primary hover:bg-atlas-primaryHover text-white px-[18px] py-2.5 rounded-full text-[13.5px] font-medium whitespace-nowrap"
        >
          <span className="w-2 h-2 rounded-full bg-atlas-danger" />
          Start new recording
        </button>
      </div>

      {showForm && (
        <RecordingFormSectionA
          onCancel={() => setShowForm(false)}
          onSubmit={handleCreate}
          isSubmitting={createRequest.isPending}
          error={createRequest.isError ? (createRequest.error as Error).message : undefined}
        />
      )}

      {requests.length > 0 ? (
        <div className="bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
          <div className="grid grid-cols-[1.7fr_1.2fr_.7fr_auto] gap-5 px-[22px] py-3 border-b border-atlas-border bg-[#fafbfa] text-[11px] tracking-wider uppercase text-atlas-muted">
            <div>Task</div><div>Target portal</div><div>Status</div><div className="w-[104px]" />
          </div>
          {requests.map((r) => (
            <div key={r.id} className="grid grid-cols-[1.7fr_1.2fr_.7fr_auto] gap-5 px-[22px] py-[18px] border-b border-atlas-rowBorder items-center hover:bg-[#fbfcfb]">
              <div className="flex items-center gap-2">
                {r.lob && (
                  <span className="text-[10.5px] font-semibold uppercase tracking-wider text-atlas-primary bg-atlas-accentSubtle px-2 py-0.5 rounded-full flex-none">
                    {r.lob}
                  </span>
                )}
                <div className="text-sm font-medium tracking-tight truncate">{r.title}</div>
              </div>
              <div className="text-[12.5px] text-atlas-body truncate">{r.target_url}</div>
              <div><StatusPill status={r.status} /></div>
              <button
                onClick={() => startRecording(r)}
                className="w-[104px] text-center text-[12.5px] py-2 rounded-full border border-atlas-borderHover text-atlas-primary font-medium hover:bg-atlas-accentSubtle"
              >
                Record
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="bg-atlas-card border border-atlas-border rounded-2xl py-20 px-10 flex flex-col items-center gap-3.5 text-center">
          <div className="w-[54px] h-[54px] rounded-[18px] bg-atlas-accentSubtle flex items-center justify-center">
            <Inbox size={24} className="text-atlas-primary" />
          </div>
          <div className="font-display text-[19px] font-semibold tracking-tight">No open requests</div>
          <div className="text-[13.5px] text-atlas-body max-w-[380px]">
            Record a flow on your own initiative with the button above, or wait for one to land here.
          </div>
        </div>
      )}
    </div>
  )
}
