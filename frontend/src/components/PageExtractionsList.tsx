import type { PageExtraction } from '../lib/extractions'

interface Props {
  extractions: PageExtraction[]
}

function downloadExtraction(extraction: PageExtraction) {
  const blob = new Blob([JSON.stringify(extraction.data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `extraction-${extraction.id}.json`
  link.click()
  URL.revokeObjectURL(url)
}

export function PageExtractionsList({ extractions }: Props) {
  if (extractions.length === 0) {
    return <div className="text-[13px] text-atlas-muted">No page extractions for this recording.</div>
  }

  return (
    <div className="bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
      <div className="px-[18px] py-3.5 border-b border-atlas-border text-[13.5px] font-semibold">
        Page extractions
      </div>
      {extractions.map((extraction) => (
        <div
          key={extraction.id}
          className="flex items-center justify-between gap-3.5 px-[18px] py-3 border-b border-atlas-rowBorder last:border-b-0"
        >
          <div className="text-[12.5px] text-atlas-body truncate">{extraction.url}</div>
          <button
            onClick={() => downloadExtraction(extraction)}
            className="text-[12.5px] py-1.5 px-3 rounded-full border border-atlas-borderHover text-atlas-primary font-medium hover:bg-atlas-accentSubtle flex-none"
          >
            Download
          </button>
        </div>
      ))}
    </div>
  )
}
