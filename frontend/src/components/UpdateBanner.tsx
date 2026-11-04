import { useState } from 'react'
import { useApplyUpdate, useUpdateCheck, useUpdateProgress } from '../lib/update'

function progressLabel(phase: string, percent: number | null): string {
  if (phase === 'relaunching') return 'Relaunching…'
  if (phase === 'error') return 'Update failed'
  return percent === null ? 'Downloading…' : `Downloading… ${percent}%`
}

export function UpdateBanner() {
  const { data } = useUpdateCheck()
  const applyUpdate = useApplyUpdate()
  const [dismissed, setDismissed] = useState(false)
  const [applying, setApplying] = useState(false)
  const progress = useUpdateProgress(applying)

  if (!data?.update_available || dismissed) return null

  const percent =
    progress.data?.total_bytes ? Math.round((progress.data.bytes_downloaded / progress.data.total_bytes) * 100) : null

  return (
    <div className="flex items-center justify-between gap-3 bg-red-600 text-white border-b border-atlas-border px-4 py-2 text-[13px]">
      <div>
        <span className="font-semibold">Version {data.latest_version} available.</span>
        {data.notes && <span className="text-white/80 ml-2">{data.notes}</span>}
      </div>
      <div className="flex items-center gap-2">
        {applying ? (
          <div className="flex items-center gap-2 w-48">
            <div className="flex-1 h-2 rounded-full bg-white/30 overflow-hidden">
              <div
                className="h-full bg-white transition-all"
                style={{ width: `${percent ?? 0}%` }}
              />
            </div>
            <span className="text-[12.5px] whitespace-nowrap">
              {progress.data ? progressLabel(progress.data.phase, percent) : 'Updating…'}
            </span>
          </div>
        ) : (
          <button
            onClick={() => {
              setApplying(true)
              applyUpdate.mutate()
            }}
            className="px-3 py-1 rounded-[8px] bg-atlas-primary text-white text-[12.5px] font-medium"
          >
            Update now
          </button>
        )}
        <button onClick={() => setDismissed(true)} className="text-white/80 text-[12.5px]">
          Dismiss
        </button>
      </div>
    </div>
  )
}
