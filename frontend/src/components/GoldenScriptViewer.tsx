import { StatusPill } from './StatusPill'
import type { GoldenScriptSummary } from '../lib/authoring'

interface Props {
  goldenScript: GoldenScriptSummary | null
}

export function GoldenScriptViewer({ goldenScript }: Props) {
  if (goldenScript === null) {
    return <div className="text-[13px] text-atlas-muted">No golden script yet.</div>
  }

  return (
    <div className="shrink-0 bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
      <div className="px-[18px] py-3.5 border-b border-atlas-border flex items-center justify-between">
        <div className="text-[13.5px] font-semibold">Golden script v{goldenScript.version}</div>
        <StatusPill status={goldenScript.status} />
      </div>
      {goldenScript.script_text !== null ? (
        <pre className="p-[18px] text-[12.5px] font-mono overflow-auto max-h-[420px] whitespace-pre">
          {goldenScript.script_text}
        </pre>
      ) : (
        <div className="p-[18px] text-[13px] text-atlas-muted">Script file not found on disk.</div>
      )}
    </div>
  )
}
