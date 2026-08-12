import { useRef, useState } from 'react'
import { Pause, Play, SkipBack, SkipForward, VideoOff } from 'lucide-react'
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

export function nextStepIndex(steps: RecordingStep[], currentIndex: number, direction: 'prev' | 'next'): number {
  if (steps.length === 0) return -1
  const target = direction === 'next' ? currentIndex + 1 : currentIndex - 1
  return Math.max(0, Math.min(steps.length - 1, target))
}

interface Props {
  recordingId: string
  pageIndex: number
  hasVideo: boolean
  steps: RecordingStep[]
  durationSeconds: number | null
}

export function VideoStepPlayer({ recordingId, pageIndex, hasVideo, steps, durationSeconds }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [browserDuration, setBrowserDuration] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const active = activeStepIndex(steps, currentTime)

  // Some .webm files report duration as Infinity until the browser has
  // fully resolved it (a known MediaSource quirk) — the backend's own
  // durationSeconds is always a real number, so prefer it and only fall
  // back to whatever the <video> element reports if that's finite.
  const knownDuration = durationSeconds ?? (Number.isFinite(browserDuration) ? browserDuration : 0)

  function seekTo(offsetMs: number | null) {
    if (!videoRef.current || offsetMs === null) return
    videoRef.current.currentTime = offsetMs / 1000
    videoRef.current.play()
  }

  function togglePlay() {
    const video = videoRef.current
    if (!video) return
    if (video.paused) video.play()
    else video.pause()
  }

  function skipToStep(direction: 'prev' | 'next') {
    const target = nextStepIndex(steps, active, direction)
    if (target === -1) return
    seekTo(steps[target].offset_ms)
  }

  function handleTimelineClick(event: React.MouseEvent<HTMLDivElement>) {
    const video = videoRef.current
    if (!video || !knownDuration) return
    const rect = event.currentTarget.getBoundingClientRect()
    const pct = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
    video.currentTime = pct * knownDuration
  }

  const progressPct = knownDuration > 0 ? (currentTime / knownDuration) * 100 : 0

  return (
    <div className="grid grid-cols-[1.65fr_1fr] gap-6 items-start">
      <div className="flex flex-col gap-3.5">
        {hasVideo ? (
          <>
            <video
              ref={videoRef}
              src={recordingVideoUrl(recordingId, pageIndex)}
              className="rounded-2xl overflow-hidden border border-atlas-border bg-black aspect-video w-full cursor-pointer"
              onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
              onDurationChange={(e) => setBrowserDuration(e.currentTarget.duration)}
              onPlay={() => setIsPlaying(true)}
              onPause={() => setIsPlaying(false)}
              onClick={togglePlay}
            />
            <div className="bg-atlas-card border border-atlas-border rounded-2xl p-4 flex items-center gap-3.5">
              <button
                onClick={togglePlay}
                className="w-9 h-9 rounded-full bg-atlas-primary flex items-center justify-center flex-none"
              >
                {isPlaying ? <Pause size={14} className="text-white" fill="white" /> : <Play size={14} className="text-white" fill="white" />}
              </button>

              <div
                className="flex-1 relative h-[34px] flex items-center cursor-pointer"
                onClick={handleTimelineClick}
              >
                <div className="absolute left-0 right-0 h-2 rounded-full bg-atlas-rowBorder" />
                <div
                  className="absolute left-0 h-2 rounded-full bg-atlas-accent"
                  style={{ width: `${progressPct}%` }}
                />
                {knownDuration > 0 && steps.map((step) => {
                  if (step.offset_ms === null) return null
                  const pct = Math.min(100, (step.offset_ms / 1000 / knownDuration) * 100)
                  return (
                    <div
                      key={step.order_index}
                      title={step.caption ?? undefined}
                      onClick={(e) => { e.stopPropagation(); seekTo(step.offset_ms) }}
                      className="absolute top-1.5 w-[3px] h-5 rounded bg-atlas-primary/70 hover:bg-atlas-primary cursor-pointer"
                      style={{ left: `${pct}%` }}
                    />
                  )
                })}
                <div
                  className="absolute top-0.5 w-1 h-[30px] rounded bg-atlas-ink pointer-events-none"
                  style={{ left: `${progressPct}%` }}
                />
              </div>

              <div className="flex items-center gap-1 flex-none">
                <button
                  onClick={() => skipToStep('prev')}
                  disabled={steps.length === 0}
                  title="Previous step"
                  className="w-8 h-8 rounded-full flex items-center justify-center hover:bg-atlas-bg text-atlas-body disabled:opacity-40"
                >
                  <SkipBack size={15} />
                </button>
                <button
                  onClick={() => skipToStep('next')}
                  disabled={steps.length === 0}
                  title="Next step"
                  className="w-8 h-8 rounded-full flex items-center justify-center hover:bg-atlas-bg text-atlas-body disabled:opacity-40"
                >
                  <SkipForward size={15} />
                </button>
              </div>

              <div className="text-[12.5px] text-atlas-body tabular-nums flex-none">
                {formatDuration(Math.floor(currentTime))} / {formatDuration(Math.floor(knownDuration))}
              </div>
            </div>
          </>
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
