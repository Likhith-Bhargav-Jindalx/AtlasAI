import { useNavigate } from 'react-router-dom'
import { VideoOff } from 'lucide-react'
import { useAuth } from '../../auth/AuthContext'
import { useRecordings } from '../../lib/recordings'
import { StatusPill } from '../../components/StatusPill'
import { formatDuration, formatRelativeTime } from '../../lib/format'

export function AgentLibraryPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  // User is guaranteed to be defined by ProtectedRoute, but may be undefined
  // during first render. We pass defaults and check user existence below.
  const { data: recordings, isLoading } = useRecordings(user?.org_id || '', undefined, user?.user_id)

  if (!user || isLoading) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  return (
    <div className="p-9 pb-16 flex flex-col gap-7 max-w-[1180px]">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-display text-[30px] font-semibold tracking-tight m-0">Recording library</h1>
        <div className="text-sm text-atlas-body">Your past recordings — video and steps.</div>
      </div>

      <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-5">
        {(recordings ?? []).map((r) => (
          <button
            key={r.id}
            onClick={() => navigate(`/agent/recordings/${r.id}`)}
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
    </div>
  )
}
