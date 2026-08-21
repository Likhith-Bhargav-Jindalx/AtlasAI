import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Search, X } from 'lucide-react'
import {
  ACTION_OPTIONS, CLIENT_OPTIONS, EHR_OPTIONS, LOB_TABS,
  SPECIFICITY_OPTIONS, suggestWorkflowName, type Lob,
} from '../lib/recordingFormSchema'

export interface SectionAValues {
  targetUrl: string
  workflowName: string
  lob: Lob
  client: string
  action: string
  specificTo: string
  ehr: string
}

interface Props {
  onSubmit: (values: SectionAValues) => void
  onCancel: () => void
  isSubmitting: boolean
  error?: string
}

const inputClass = 'px-3.5 py-2.5 rounded-[10px] border border-atlas-border outline-none focus:border-atlas-accent text-sm w-full'
const labelClass = 'text-[12px] font-medium text-atlas-body'

// ---------------------------------------------------------------------------
// Searchable dropdown — type-to-filter with click selection
// ---------------------------------------------------------------------------

function SearchableSelect({
  label,
  options,
  value,
  onChange,
  placeholder = 'Type to search…',
}: {
  label: string
  options: string[]
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const filtered = query
    ? options.filter((o) => o.toLowerCase().includes(query.toLowerCase()))
    : options

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  function select(o: string) {
    onChange(o)
    setQuery('')
    setOpen(false)
  }

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    setQuery(e.target.value)
    if (!open) setOpen(true)
  }

  function toggleOpen() {
    setOpen((v) => !v)
    if (!open) setTimeout(() => inputRef.current?.focus(), 0)
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label className={labelClass}>{label}</label>
      <div ref={containerRef} className="relative">
        <button
          type="button"
          onClick={toggleOpen}
          className={`${inputClass} flex items-center justify-between gap-2 text-left cursor-pointer`}
        >
          <span className={value ? 'text-atlas-text' : 'text-atlas-muted'}>{value || placeholder}</span>
          <ChevronDown size={14} className="text-atlas-muted flex-none" />
        </button>

        {open && (
          <div className="absolute z-50 mt-1 w-full bg-atlas-card border border-atlas-border rounded-[10px] shadow-lg overflow-hidden">
            <div className="flex items-center gap-2 px-3 py-2 border-b border-atlas-border">
              <Search size={14} className="text-atlas-muted flex-none" />
              <input
                ref={inputRef}
                value={query}
                onChange={handleInputChange}
                placeholder="Search…"
                className="text-sm outline-none flex-1 bg-transparent"
                autoFocus
              />
            </div>
            <div className="max-h-48 overflow-y-auto">
              {filtered.length > 0 ? (
                filtered.map((o) => (
                  <button
                    key={o}
                    type="button"
                    onClick={() => select(o)}
                    className={`w-full text-left px-3 py-2 text-[13px] hover:bg-atlas-accentSubtle ${
                      o === value ? 'bg-atlas-accentSubtle font-medium text-atlas-primary' : 'text-atlas-text'
                    }`}
                  >
                    {o}
                  </button>
                ))
              ) : (
                <div className="px-3 py-3 text-[13px] text-atlas-muted text-center">No results</div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main form component
// ---------------------------------------------------------------------------

export function RecordingFormSectionA({ onSubmit, onCancel, isSubmitting, error }: Props) {
  const [targetUrl, setTargetUrl] = useState('')
  const [workflowName, setWorkflowName] = useState('')
  const [workflowNameTouched, setWorkflowNameTouched] = useState(false)
  const [lob, setLob] = useState<Lob>('AR')
  const [client, setClient] = useState('')
  const [action, setAction] = useState('')
  const [specificTo, setSpecificTo] = useState('')
  const [ehr, setEhr] = useState('')

  // Auto-suggest title when inputs change (unless user has manually edited it)
  useEffect(() => {
    if (!workflowNameTouched) {
      setWorkflowName(suggestWorkflowName(client || null, action || null, ehr || null))
    }
  }, [client, action, ehr, workflowNameTouched])

  function selectLob(next: Lob) {
    setLob(next)
    setClient('')
    setAction('')
    setEhr('')
    setWorkflowNameTouched(false)
  }

  const canSubmit = targetUrl.trim() !== ''

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-6">
      <div className="bg-atlas-card rounded-2xl w-full max-w-xl max-h-[90vh] overflow-y-auto flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-atlas-border flex-none">
          <div className="font-display text-lg font-semibold tracking-tight">Start new recording</div>
          <button onClick={onCancel} className="w-8 h-8 rounded-full flex items-center justify-center hover:bg-atlas-bg text-atlas-body">
            <X size={16} />
          </button>
        </div>

        {/* LOB tabs */}
        <div className="flex px-6 pt-4 gap-1">
          {LOB_TABS.map((tab) => (
            <button
              key={tab.lob}
              onClick={() => selectLob(tab.lob)}
              className={`px-4 py-2 rounded-full text-[13px] font-medium border ${
                lob === tab.lob
                  ? 'bg-atlas-primary text-white border-atlas-primary'
                  : 'border-atlas-border text-atlas-body hover:bg-atlas-bg'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="p-6 flex flex-col gap-4">
          {/* Select Client */}
          <SearchableSelect
            label="Select Client"
            options={CLIENT_OPTIONS[lob]}
            value={client}
            onChange={setClient}
            placeholder="Type to search…"
          />

          {/* Select Action */}
          <SearchableSelect
            label="Select Action"
            options={ACTION_OPTIONS[lob]}
            value={action}
            onChange={setAction}
            placeholder="Type to search…"
          />

          {/* Specific to */}
          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>Specific to</label>
            <div className="flex gap-2">
              {SPECIFICITY_OPTIONS.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  onClick={() => setSpecificTo(specificTo === opt ? '' : opt)}
                  className={`px-4 py-2 rounded-full text-[13px] font-medium border ${
                    specificTo === opt
                      ? 'bg-atlas-primary text-white border-atlas-primary'
                      : 'border-atlas-border text-atlas-body hover:bg-atlas-bg'
                  }`}
                >
                  {opt}
                </button>
              ))}
            </div>
          </div>

          {/* Select EHR */}
          <SearchableSelect
            label="Select EHR"
            options={EHR_OPTIONS[lob]}
            value={ehr}
            onChange={setEhr}
            placeholder="Type to search…"
          />

          {/* Title (auto-generated) */}
          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>Title</label>
            <input
              value={workflowName}
              onChange={(e) => { setWorkflowName(e.target.value); setWorkflowNameTouched(true) }}
              placeholder="Client + Action + EHR (auto-generated)"
              className={inputClass}
            />
          </div>

          {/* Target Portal URL */}
          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>Target portal URL</label>
            <input
              required type="url" placeholder="https://portal.example.com/..."
              value={targetUrl} onChange={(e) => setTargetUrl(e.target.value)} className={inputClass}
            />
          </div>

          {error && <div className="text-[13px] text-atlas-diffOldText">{error}</div>}

          <button
            disabled={!canSubmit || isSubmitting}
            onClick={() => {
              const effectiveTitle = workflowName.trim() || suggestWorkflowName(client || null, action || null, ehr || null) || targetUrl.trim()
              onSubmit({ targetUrl: targetUrl.trim(), workflowName: effectiveTitle, lob, client, action, specificTo, ehr })
            }}
            className="bg-atlas-primary hover:bg-atlas-primaryHover text-white rounded-full py-2.5 text-[13.5px] font-medium disabled:opacity-60"
          >
            {isSubmitting ? 'Starting…' : 'Create and start recording'}
          </button>
        </div>
      </div>
    </div>
  )
}
