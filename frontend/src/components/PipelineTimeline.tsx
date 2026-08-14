import { Check, Loader2, X } from 'lucide-react'
import { PHASE_ORDER, phaseIndex } from './AuthoringPhaseTracker'
import type { AuthoringRunDetail } from '../lib/authoring'

const PHASE_META: Record<string, { title: string; description: string }> = {
  replay: { title: 'Replay', description: 'Re-run the recorded flow, watching for drift' },
  extract: { title: 'Extract', description: 'Fold developer corrections into the trajectory' },
  brain: { title: 'Brain', description: 'Verify every step and repair off-track selectors' },
  golden: { title: 'Golden', description: 'Freeze the verified steps into a golden script' },
  certifying: { title: 'Certifying', description: 'Run the golden end-to-end to prove it works' },
  done: { title: 'Done', description: 'Golden certified and stored' },
}

interface Props {
  run: AuthoringRunDetail
}

export function PipelineTimeline({ run }: Props) {
  const activeIndex = phaseIndex(run.current_phase)
  const failed = run.status === 'failed'
  const cancelled = run.status === 'cancelled'
  const completed = run.status === 'completed'

  return (
    <div className="bg-atlas-card border border-atlas-border rounded-2xl p-[18px]">
      <div className="text-[13.5px] font-semibold mb-4">Pipeline</div>
      <div className="flex flex-col">
        {PHASE_ORDER.map((phase, i) => {
          const isLast = i === PHASE_ORDER.length - 1
          let state: 'done' | 'running' | 'failed' | 'pending'
          if (failed || cancelled) {
            state = i === activeIndex ? 'failed' : i < activeIndex ? 'done' : 'pending'
          } else if (i < activeIndex) {
            state = 'done'
          } else if (i === activeIndex) {
            state = completed ? 'done' : 'running'
          } else {
            state = 'pending'
          }
          const meta = PHASE_META[phase]

          return (
            <div key={phase} className="flex gap-3">
              <div className="flex flex-col items-center">
                <span
                  className={`flex h-6 w-6 items-center justify-center rounded-full border ${
                    state === 'done'
                      ? 'border-atlas-primary bg-atlas-accentSubtle text-atlas-primary'
                      : state === 'failed'
                        ? 'border-atlas-diffOldText bg-atlas-diffOldText/10 text-atlas-diffOldText'
                        : state === 'running'
                          ? 'border-atlas-primary bg-atlas-primary text-white'
                          : 'border-atlas-border text-atlas-muted'
                  }`}
                >
                  {state === 'done' ? (
                    <Check size={13} />
                  ) : state === 'failed' ? (
                    <X size={13} />
                  ) : state === 'running' ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : (
                    <span className="h-1.5 w-1.5 rounded-full bg-current" />
                  )}
                </span>
                {!isLast && <span className={`w-px flex-1 ${state === 'done' ? 'bg-atlas-primary/40' : 'bg-atlas-border'}`} />}
              </div>
              <div className={`pb-4 ${isLast ? 'pb-0' : ''}`}>
                <div className="text-[13px] font-medium">{meta.title}</div>
                <div className="text-[12px] text-atlas-muted">{meta.description}</div>
                {state !== 'pending' && (
                  <div
                    className={`mt-0.5 text-[11px] font-medium ${
                      state === 'done'
                        ? 'text-atlas-primary'
                        : state === 'failed'
                          ? 'text-atlas-diffOldText'
                          : 'text-atlas-primary'
                    }`}
                  >
                    {state === 'running' ? 'running' : state}
                  </div>
                )}
              </div>
            </div>
          )
        })}
        {run.repair_attempt > 0 && run.status !== 'completed' && (
          <div className="mt-2 text-[11.5px] text-atlas-muted">
            repair attempt {run.repair_attempt}/2
          </div>
        )}
      </div>
    </div>
  )
}