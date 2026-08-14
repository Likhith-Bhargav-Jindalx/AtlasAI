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
