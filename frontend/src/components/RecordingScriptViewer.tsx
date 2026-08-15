import { useState } from 'react'
import { Copy, Check, Download, FileCode, Maximize2, Minimize2 } from 'lucide-react'

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
  const [expanded, setExpanded] = useState(false)

  function copy() {
    if (!script) return
    void navigator.clipboard.writeText(script).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  const lines = script ? script.split('\n') : []

  return (
    <div className="shrink-0 bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden shadow-sm">
      <div className="flex items-center justify-between gap-3 px-[18px] py-3.5 border-b border-atlas-border bg-atlas-card">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-atlas-accentSubtle flex items-center justify-center text-atlas-primary">
            <FileCode size={15} />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[13.5px] font-semibold tracking-tight">Recorded Playwright script</span>
            <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-atlas-bg border border-atlas-border text-atlas-muted">
              Python
            </span>
            {lines.length > 0 && (
              <span className="text-[11px] text-atlas-muted">
                {lines.length} lines
              </span>
            )}
          </div>
        </div>
        {script && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => setExpanded((prev) => !prev)}
              className="text-[12.5px] py-1.5 px-3 rounded-full border border-atlas-borderHover text-atlas-body font-medium hover:bg-atlas-bg flex items-center gap-1.5 transition-colors"
              title={expanded ? 'Collapse height' : 'Expand full height'}
            >
              {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
              {expanded ? 'Collapse' : 'Expand'}
            </button>
            <button
              onClick={copy}
              className="text-[12.5px] py-1.5 px-3 rounded-full border border-atlas-borderHover text-atlas-primary font-medium hover:bg-atlas-accentSubtle flex items-center gap-1.5 transition-colors"
            >
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
            <button
              onClick={() => downloadScript(script)}
              className="text-[12.5px] py-1.5 px-3 rounded-full border border-atlas-borderHover text-atlas-primary font-medium hover:bg-atlas-accentSubtle flex items-center gap-1.5 transition-colors"
            >
              <Download size={13} />
              Download .py
            </button>
          </div>
        )}
      </div>
      {script ? (
        <div
          className={`overflow-auto p-4 font-mono text-[12px] leading-relaxed text-atlas-body bg-atlas-bg/40 ${
            expanded ? 'max-h-[800px]' : 'max-h-[380px]'
          }`}
        >
          <table className="w-full border-collapse">
            <tbody>
              {lines.map((line, idx) => (
                <tr key={idx} className="hover:bg-atlas-card/60">
                  <td className="pr-4 py-0.5 text-right select-none text-atlas-muted/60 text-[11px] w-8 tabular-nums">
                    {idx + 1}
                  </td>
                  <td className="py-0.5 whitespace-pre font-mono text-atlas-body">
                    {line || ' '}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="px-[18px] py-6 text-[13px] text-atlas-muted">
          No recorded script — this recording predates script capture.
        </div>
      )}
    </div>
  )
}