export const PHASE_ORDER = ['replay', 'extract', 'brain', 'golden', 'certifying', 'done']

export function phaseIndex(phase: string): number {
  return PHASE_ORDER.indexOf(phase)
}

const PHASE_LABELS: Record<string, string> = {
  replay: 'Replay',
  extract: 'Extract',
  brain: 'Brain',
  golden: 'Golden',
  certifying: 'Certifying',
  done: 'Done',
}

interface Props {
  currentPhase: string
  status: string
  repairAttempt: number
}

export function AuthoringPhaseTracker({ currentPhase, status, repairAttempt }: Props) {
  const activeIndex = phaseIndex(currentPhase)
  const failed = status === 'failed'
  const cancelled = status === 'cancelled'

  return (
    <div className="flex items-center gap-2">
      {PHASE_ORDER.map((phase, i) => {
        const isPast = i < activeIndex
        const isActive = i === activeIndex && !failed && !cancelled
        const isErrored = i === activeIndex && (failed || cancelled)
        return (
          <div key={phase} className="flex items-center gap-2">
            <div
              className={`px-2.5 py-1 rounded-full text-[11.5px] font-medium ${
                isErrored
                  ? 'bg-atlas-diffOldText/10 text-atlas-diffOldText'
                  : isActive
                    ? 'bg-atlas-primary text-white'
                    : isPast
                      ? 'bg-atlas-accentSubtle text-atlas-primary'
                      : 'bg-atlas-bg text-atlas-muted'
              }`}
            >
              {PHASE_LABELS[phase]}
            </div>
            {i < PHASE_ORDER.length - 1 && <div className="w-4 h-px bg-atlas-border" />}
          </div>
        )
      })}
      {repairAttempt > 0 && status !== 'completed' && (
        <span className="text-[11.5px] text-atlas-muted ml-1">repair attempt {repairAttempt}/2</span>
      )}
    </div>
  )
}
