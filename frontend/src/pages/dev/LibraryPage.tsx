import { useNavigate } from 'react-router-dom'
import { VideoOff } from 'lucide-react'
import { useAuth } from '../../auth/AuthContext'
import { useRecordings } from '../../lib/recordings'
import { StatusPill } from '../../components/StatusPill'
import { formatDuration, formatRelativeTime } from '../../lib/format'

export function LibraryPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const { data: recordings, isLoading } = useRecordings(user!.org_id)

  if (isLoading) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  return (
    <div className="p-9 pb-16 flex flex-col gap-[26px] max-w-[1280px]">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-display text-[30px] font-semibold tracking-tight m-0">Recording library</h1>
        <div className="text-sm text-atlas-body">Recordings waiting to become golden scripts.</div>
      </div>

      {recordings && recordings.length > 0 ? (
        <div className="grid grid-cols-3 gap-5">
          {recordings.map((r) => (
            <button
              key={r.id}
              onClick={() => navigate(`/dev/recordings/${r.id}`)}
              className="text-left bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden hover:shadow-lg hover:-translate-y-0.5 transition-all"
            >
              <div className="aspect-video bg-[#e8eae8] relative flex items-center justify-center">
                {!r.has_video && <VideoOff size={24} className="text-atlas-muted" />}
                <div className="absolute right-2.5 bottom-2.5 text-[11px] text-white bg-black/55 px-2 py-0.5 rounded-full tabular-nums">
                  {formatDuration(r.duration_seconds)}
                </div>
              </div>
              <div className="p-4 flex flex-col gap-2.5">
                <div className="flex items-start justify-between gap-2.5">
                  <div className="text-[14.5px] font-medium tracking-tight">{r.request_title ?? r.portal_domain ?? 'Recording'}</div>
                  <StatusPill status={r.status} />
                </div>
                <div className="text-[12.5px] text-atlas-body">{r.portal_domain}</div>
                <div className="text-[12px] text-atlas-muted pt-1.5 border-t border-atlas-rowBorder">
                  {formatRelativeTime(r.created_at)}
                </div>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="bg-atlas-card border border-atlas-border rounded-2xl py-20 px-10 flex flex-col items-center gap-3.5 text-center">
          <div className="w-[54px] h-[54px] rounded-[18px] bg-atlas-accentSubtle flex items-center justify-center">
            <VideoOff size={24} className="text-atlas-primary" />
          </div>
          <div className="font-display text-[19px] font-semibold tracking-tight">No recordings yet</div>
          <div className="text-[13.5px] text-atlas-body max-w-[400px]">
            Ask an operations agent to record a workflow, or record it yourself.
          </div>
        </div>
      )}
    </div>
  )
}
