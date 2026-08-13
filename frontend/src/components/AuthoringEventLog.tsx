import { useEffect, useRef } from 'react'
import type { AuthoringRunEvent } from '../lib/authoring'

const LEVEL_COLORS: Record<string, string> = {
  info: 'text-atlas-body',
  warning: 'text-atlas-muted',
  error: 'text-atlas-diffOldText',
}

export function levelColorClass(level: string): string {
  return LEVEL_COLORS[level] ?? LEVEL_COLORS.info
}

interface Props {
  events: AuthoringRunEvent[]
}

export function AuthoringEventLog({ events }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // New events append every poll during a live run; keep the newest line in
  // view instead of leaving it below the fold.
  useEffect(() => {
    const container = scrollRef.current
    if (container) container.scrollTop = container.scrollHeight
  }, [events.length])

  if (events.length === 0) {
    return <div className="text-[13px] text-atlas-muted">No events yet.</div>
  }

  return (
    <div className="bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
      <div className="px-[18px] py-3.5 border-b border-atlas-border text-[13.5px] font-semibold">Log</div>
      <div ref={scrollRef} className="max-h-[280px] overflow-auto flex flex-col">
        {events.map((event, i) => (
          <div
            key={i}
            className={`px-[18px] py-2.5 border-b border-atlas-rowBorder text-[12.5px] font-mono ${levelColorClass(event.level)}`}
          >
            <span className="text-atlas-muted">[{event.phase}]</span>{' '}
            {event.step_index !== null && <span className="text-atlas-muted">step {event.step_index}: </span>}
            {event.message}
          </div>
        ))}
      </div>
    </div>
  )
}
