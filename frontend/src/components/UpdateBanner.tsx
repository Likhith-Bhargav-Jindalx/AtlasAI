import { useState } from 'react'
import { useApplyUpdate, useUpdateCheck } from '../lib/update'

export function UpdateBanner() {
  const { data } = useUpdateCheck()
  const applyUpdate = useApplyUpdate()
  const [dismissed, setDismissed] = useState(false)

  if (!data?.update_available || dismissed) return null

  return (
    <div className="flex items-center justify-between gap-3 bg-red-600 text-white border-b border-atlas-border px-4 py-2 text-[13px]">
      <div>
        <span className="font-semibold">Version {data.latest_version} available.</span>
        {data.notes && <span className="text-white/80 ml-2">{data.notes}</span>}
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => applyUpdate.mutate()}
          disabled={applyUpdate.isPending}
          className="px-3 py-1 rounded-[8px] bg-atlas-primary text-white text-[12.5px] font-medium disabled:opacity-60"
        >
          {applyUpdate.isPending ? 'Updating…' : 'Update now'}
        </button>
        <button onClick={() => setDismissed(true)} className="text-white/80 text-[12.5px]">
          Dismiss
        </button>
      </div>
    </div>
  )
}
