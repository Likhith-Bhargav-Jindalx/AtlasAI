import { useRef, useState } from 'react'
import { VideoOff } from 'lucide-react'
import { recordingVideoUrl } from '../lib/recordings'
import { formatDuration } from '../lib/format'
import type { RecordingStep } from '../lib/recordings'

export function activeStepIndex(steps: RecordingStep[], currentTimeSeconds: number): number {
  let active = -1
  for (let i = 0; i < steps.length; i++) {
    const offsetSeconds = (steps[i].offset_ms ?? 0) / 1000
    if (offsetSeconds <= currentTimeSeconds) active = i
  }
  return active
}

interface Props {
  recordingId: string
  hasVideo: boolean
  steps: RecordingStep[]
  durationSeconds: number | null
}

export function VideoStepPlayer({ recordingId, hasVideo, steps, durationSeconds }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const active = activeStepIndex(steps, currentTime)

  function seekTo(offsetMs: number | null) {
    if (!videoRef.current || offsetMs === null) return
    videoRef.current.currentTime = offsetMs / 1000
    videoRef.current.play()
  }

  return (
    <div className="grid grid-cols-[1.65fr_1fr] gap-6 items-start">
      <div className="flex flex-col gap-3.5">
        {hasVideo ? (
          <video
            ref={videoRef}
            src={recordingVideoUrl(recordingId)}
            controls
            className="rounded-2xl overflow-hidden border border-atlas-border bg-black aspect-video w-full"
            onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
          />
        ) : (
          <div className="rounded-2xl border border-atlas-border bg-atlas-bg aspect-video flex flex-col items-center justify-center gap-2 text-atlas-muted">
            <VideoOff size={28} />
            <div className="text-[13px]">No video available for this recording</div>
          </div>
        )}
      </div>

      <div className="bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
        <div className="px-[18px] py-3.5 border-b border-atlas-border flex items-center justify-between">
          <div className="text-[13.5px] font-semibold">Steps</div>
          <div className="text-xs text-atlas-muted">{formatDuration(durationSeconds)}</div>
        </div>
        <div className="max-h-[520px] overflow-auto">
          {steps.length === 0 && (
            <div className="p-[18px] text-[13px] text-atlas-body">No actions were recorded.</div>
          )}
          {steps.map((step, i) => (
            <button
              key={step.order_index}
              onClick={() => seekTo(step.offset_ms)}
              disabled={!hasVideo}
              className={`w-full text-left flex gap-3 px-[18px] py-3.5 border-b border-atlas-rowBorder hover:bg-[#fbfcfb] ${
                i === active ? 'bg-atlas-accentSubtle' : ''
              }`}
            >
              <div className="w-[22px] h-[22px] flex-none rounded-full bg-atlas-accentSubtle text-atlas-primary text-[11px] flex items-center justify-center tabular-nums">
                {step.order_index + 1}
              </div>
              <div className="flex flex-col gap-0.5 min-w-0">
                <div className="text-[13px]">{step.caption ?? 'Action'}</div>
                {step.offset_ms !== null && (
                  <div className="text-[11.5px] text-atlas-muted tabular-nums">
                    {formatDuration(Math.round(step.offset_ms / 1000))}
                  </div>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
