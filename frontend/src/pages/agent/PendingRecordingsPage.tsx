import { useNavigate } from 'react-router-dom'
import { VideoOff } from 'lucide-react'
import { usePendingRecordings } from '../../lib/pendingRecordings'
import { formatDuration, formatRelativeTime } from '../../lib/format'

export function PendingRecordingsPage() {
  const navigate = useNavigate()
  const { data: pending, isLoading } = usePendingRecordings()

  if (isLoading) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  return (
    <div className="p-9 pb-16 flex flex-col gap-7 max-w-[1180px]">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-display text-[30px] font-semibold tracking-tight m-0">Pending uploads</h1>
        <div className="text-sm text-atlas-body">
          Recordings waiting on this machine — review, then upload or discard.
        </div>
      </div>

      <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-5">
        {(pending ?? []).map((p) => (
          <button
            key={p.id}
            onClick={() => navigate(`/agent/pending/${p.id}`)}
            className="text-left bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden hover:shadow-lg hover:-translate-y-0.5 transition-all"
          >
            <div className="aspect-video bg-[#e8eae8] relative flex items-center justify-center">
              {!p.has_video && <VideoOff size={24} className="text-atlas-muted" />}
              <div className="absolute right-2.5 bottom-2.5 text-[11px] text-white bg-black/55 px-2 py-0.5 rounded-full tabular-nums">
                {formatDuration(p.duration_seconds)}
              </div>
            </div>
            <div className="p-4 flex flex-col gap-2.5">
              <div className="text-[14.5px] font-medium tracking-tight">{p.start_url}</div>
              {p.upload_error && (
                <div className="text-[12px] text-atlas-danger">Last upload failed — tap to retry</div>
              )}
              <div className="flex items-center justify-between gap-2 pt-1.5 border-t border-atlas-rowBorder">
                <div className="text-[12px] text-atlas-muted">{formatRelativeTime(p.created_at)}</div>
                <div className="text-[12px] text-atlas-muted">{p.action_count} actions</div>
              </div>
            </div>
          </button>
        ))}
      </div>

      {(pending ?? []).length === 0 && (
        <div className="text-sm text-atlas-body">Nothing pending — every recording on this machine is uploaded.</div>
      )}
    </div>
  )
}
