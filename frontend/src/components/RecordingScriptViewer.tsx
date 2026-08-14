import { useState } from 'react'
import { Copy, Check, Download } from 'lucide-react'

interface Props {
  script: string | null
}

function downloadScript(script: string) {
  const blob = new Blob([script], { type: 'text/x-python' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'recording.py'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export function RecordingScriptViewer({ script }: Props) {
  const [copied, setCopied] = useState(false)

  function copy() {
    if (!script) return
    void navigator.clipboard.writeText(script).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-[18px] py-3.5 border-b border-atlas-border">
        <div className="text-[13.5px] font-semibold">Recorded Playwright script</div>
        {script && (
          <div className="flex items-center gap-2">
            <button
              onClick={copy}
              className="text-[12.5px] py-1.5 px-3 rounded-full border border-atlas-borderHover text-atlas-primary font-medium hover:bg-atlas-accentSubtle flex items-center gap-1.5"
            >
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
            <button
              onClick={() => downloadScript(script)}
              className="text-[12.5px] py-1.5 px-3 rounded-full border border-atlas-borderHover text-atlas-primary font-medium hover:bg-atlas-accentSubtle flex items-center gap-1.5"
            >
              <Download size={13} />
              Download .py
            </button>
          </div>
        )}
      </div>
      <pre className="max-h-[320px] overflow-auto px-[18px] py-3.5 font-mono text-[12px] leading-relaxed text-atlas-body">
        {script ? (
          script
        ) : (
          <span className="text-atlas-muted">No recorded script — this recording predates script capture.</span>
        )}
      </pre>
    </div>
  )
}