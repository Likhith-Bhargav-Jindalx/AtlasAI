import type { RecordingStep } from '../lib/recordings'

interface Props {
  steps: RecordingStep[]
}

export function StepsViewer({ steps }: Props) {
  if (steps.length === 0) {
    return (
      <div className="bg-atlas-card border border-atlas-border rounded-2xl p-[18px]">
        <div className="text-[13.5px] font-semibold mb-2">Steps</div>
        <div className="text-[13px] text-atlas-muted">No steps recorded.</div>
      </div>
    )
  }

  return (
    <div className="bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
      <div className="px-[18px] py-3.5 border-b border-atlas-border text-[13.5px] font-semibold">
        Steps
        <span className="ml-2 text-[12px] font-normal text-atlas-muted">({steps.length})</span>
      </div>
      <div className="p-[18px] flex flex-col gap-1.5">
        {steps.map((step) => (
          <div key={step.order_index} className="flex items-baseline gap-2.5 text-[12.5px]">
            <span className="text-atlas-muted tabular-nums w-8 shrink-0">#{step.order_index}</span>
            <span className="text-atlas-body">{step.caption ?? '—'}</span>
            {step.offset_ms !== null && (
              <span className="text-atlas-muted font-mono ml-auto">{(step.offset_ms / 1000).toFixed(1)}s</span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}