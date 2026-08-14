import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { MonitorPlay, Square } from 'lucide-react'
import { useAuth } from '../../auth/AuthContext'
import { ApiError } from '../../lib/api'
import { useStartRecording, useStopRecording } from '../../lib/recordings'

export function LiveRecordingPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const startUrl = searchParams.get('url') ?? ''
  const requestId = searchParams.get('requestId') ?? undefined

  const startRecording = useStartRecording()
  const stopRecording = useStopRecording()
  const [captureSessionId, setCaptureSessionId] = useState<string | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [stopError, setStopError] = useState<string | null>(null)
  const startedRef = useRef(false)

  useEffect(() => {
    if (startedRef.current || !startUrl) return
    startedRef.current = true
    startRecording
      .mutateAsync({ org_id: user!.org_id, start_url: startUrl, request_id: requestId, role: 'demo', user_id: user!.user_id })
      .then((result) => setCaptureSessionId(result.capture_session_id))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startUrl])

  useEffect(() => {
    if (!captureSessionId) return
    const interval = setInterval(() => setElapsedSeconds((s) => s + 1), 1000)
    return () => clearInterval(interval)
  }, [captureSessionId])

  async function handleStop() {
    if (!captureSessionId) return
    setStopError(null)
    try {
      const result = await stopRecording.mutateAsync(captureSessionId)
      navigate(`/agent/recordings/${result.recording_id}`)
    } catch (err) {
      setStopError(err instanceof ApiError ? err.message : 'Could not stop the recording. If you closed the browser window, wait a moment and try again.')
    }
  }

  const minutes = Math.floor(elapsedSeconds / 60)
  const seconds = elapsedSeconds % 60
  const elapsed = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`

  return (
    <div className="min-h-screen bg-[#0d0d0c] flex flex-col items-center px-10 pt-8 pb-10 gap-6 box-border">
      <div className="w-full max-w-[1080px] flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="w-2.5 h-2.5 rounded-full bg-atlas-danger animate-pulse" />
          <span className="text-white text-sm font-medium tracking-tight">Recording</span>
          <span className="text-atlas-body text-[13px]">{startUrl}</span>
        </div>
        <div className="text-white text-[22px] tabular-nums tracking-wide">{elapsed}</div>
      </div>

      <div className="w-full max-w-[1080px] aspect-video rounded-[18px] overflow-hidden border border-[#2a2a28] bg-[#141413] flex flex-col items-center justify-center gap-3.5 text-[#5c5c56]">
        <MonitorPlay size={34} />
        <div className="text-[13px]">Live capture in progress in a separate browser window</div>
        <div className="text-xs text-[#3f3f3b]">Switch to it, perform the flow, then come back and stop here.</div>
      </div>

      <button
        onClick={handleStop}
        disabled={!captureSessionId || stopRecording.isPending}
        className="flex items-center gap-2.5 bg-atlas-danger hover:bg-atlas-dangerHover text-white px-[26px] py-3.5 rounded-full text-sm font-semibold disabled:opacity-50"
      >
        <Square size={11} fill="white" />
        {stopRecording.isPending ? 'Stopping…' : 'Stop and review'}
      </button>

      {stopError && <div className="text-[13px] text-atlas-danger">{stopError}</div>}
    </div>
  )
}
