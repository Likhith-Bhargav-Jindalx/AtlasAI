import { Fragment, useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { useApproveHealEvent, useHealEvents, useRejectHealEvent, healEventScreenshotUrl } from '../lib/healEvents'
import { StatusPill } from '../components/StatusPill'
import { formatRelativeTime } from '../lib/format'

export function ApprovalsPage() {
  const { user, isLoading: authLoading } = useAuth()
  const { data: heals, isLoading } = useHealEvents(user?.org_id || '', 'pending_approval')
  const approve = useApproveHealEvent()
  const reject = useRejectHealEvent()
  const [selectedId, setSelectedId] = useState<string | null>(null)

  if (authLoading || isLoading) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  const selected = heals?.find((h) => h.id === selectedId) ?? heals?.[0]

  return (
    <div className="p-9 pb-16 flex flex-col gap-[26px] max-w-[1280px]">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-display text-[30px] font-semibold tracking-tight m-0">Heal approvals</h1>
        <div className="text-sm text-atlas-body">A healed version stays inactive until you approve it.</div>
      </div>

      {heals && heals.length > 0 && selected ? (
        <div className="grid grid-cols-[340px_1fr] gap-6 items-start">
          <div className="bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
            <div className="px-[18px] py-3.5 border-b border-atlas-border text-[11px] tracking-wider uppercase text-atlas-muted">
              Pending · {heals.length}
            </div>
            {heals.map((h) => (
              <button
                key={h.id}
                onClick={() => setSelectedId(h.id)}
                className={`w-full text-left px-[18px] py-4 border-b border-atlas-rowBorder flex flex-col gap-1.5 ${
                  h.id === selected.id ? 'bg-atlas-accentSubtle border-l-[3px] border-l-atlas-primary' : ''
                }`}
              >
                <div className="flex items-center justify-between gap-2.5">
                  <div className="text-[13.5px] font-medium tracking-tight">{h.portal_domain ?? 'Unknown portal'}</div>
                  <StatusPill status={h.status} />
                </div>
                <div className="text-xs text-atlas-body">
                  {h.diff_summary?.length ?? 0} step{h.diff_summary?.length === 1 ? '' : 's'} changed · {formatRelativeTime(h.created_at)}
                </div>
              </button>
            ))}
          </div>

          <div className="bg-atlas-card border border-atlas-border rounded-2xl p-6 flex flex-col gap-[18px]">
            <div className="flex items-start justify-between gap-5">
              <div className="flex flex-col gap-1.5">
                <div className="font-display text-xl font-semibold tracking-tight">{selected.portal_domain ?? 'Unknown portal'}</div>
                <div className="text-[13px] text-atlas-body">
                  {selected.trigger === 'runtime_auto' ? 'Detected automatically' : 'Requested on demand'} · {formatRelativeTime(selected.created_at)}
                </div>
              </div>
              <div className="flex gap-2.5 flex-none">
                <button
                  onClick={() => reject.mutate(selected.id)}
                  disabled={reject.isPending}
                  className="px-[18px] py-2.5 rounded-full border border-atlas-borderHover text-[13px] hover:bg-atlas-bg"
                >
                  Reject
                </button>
                <button
                  onClick={() => approve.mutate(selected.id)}
                  disabled={approve.isPending}
                  className="px-5 py-2.5 rounded-full bg-atlas-primary hover:bg-atlas-primaryHover text-white text-[13px] font-medium whitespace-nowrap"
                >
                  Approve and activate
                </button>
              </div>
            </div>

            {(approve.isError || reject.isError) && (
              <div className="text-[13px] text-atlas-diffOldText">
                {((approve.error ?? reject.error) as Error).message}
              </div>
            )}

            <div className="flex flex-col gap-3">
              {(selected.diff_summary ?? []).map((entry) => (
                <Fragment key={entry.step_index}>
                  <div key={`${entry.step_index}-shots`} className="grid grid-cols-2 gap-[18px]">
                    {entry.before_screenshot && (
                      <img
                        src={healEventScreenshotUrl(selected.id, entry.step_index, 'before')}
                        alt={`Step ${entry.step_index + 1} before`}
                        className="rounded-xl border border-atlas-border w-full"
                      />
                    )}
                    {entry.after_screenshot && (
                      <img
                        src={healEventScreenshotUrl(selected.id, entry.step_index, 'after')}
                        alt={`Step ${entry.step_index + 1} after`}
                        className="rounded-xl border border-atlas-border w-full"
                      />
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-[18px]">
                    <div className="flex flex-col gap-1.5">
                      <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Step {entry.step_index + 1} — current</div>
                      <div className="p-3 rounded-xl font-mono text-[11.5px] break-all bg-atlas-diffOldBg text-atlas-diffOldText">
                        {entry.old_value}
                      </div>
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <div className="text-[11px] tracking-wider uppercase text-atlas-primary">Step {entry.step_index + 1} — proposed</div>
                      <div className="p-3 rounded-xl font-mono text-[11.5px] break-all bg-atlas-diffNewBg text-atlas-diffNewText">
                        {entry.new_value}
                      </div>
                    </div>
                  </div>
                </Fragment>
              ))}
              {(!selected.diff_summary || selected.diff_summary.length === 0) && (
                <div className="text-[13px] text-atlas-body">No selector changes recorded for this heal event.</div>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="bg-atlas-card border border-atlas-border rounded-2xl py-20 px-10 flex flex-col items-center gap-3.5 text-center">
          <div className="w-[54px] h-[54px] rounded-[18px] bg-atlas-accentSubtle flex items-center justify-center">
            <ShieldCheck size={24} className="text-atlas-primary" />
          </div>
          <div className="font-display text-[19px] font-semibold tracking-tight">Nothing to approve</div>
          <div className="text-[13.5px] text-atlas-body max-w-[400px]">We'll surface a diff here the moment a step drifts.</div>
        </div>
      )}
    </div>
  )
}
