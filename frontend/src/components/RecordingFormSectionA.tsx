import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import {
  ACTION_OPTIONS, CATEGORY_OPTIONS, GENERIC_CLIENT_SENTINEL, LOB_TABS,
  REUSE_SCOPE_OPTIONS, suggestWorkflowName, type Lob,
} from '../lib/recordingFormSchema'
import { useClients, useSystems } from '../lib/masterData'

export interface SectionAValues {
  targetUrl: string
  workflowName: string
  lob: Lob
  category: string
  action: string
  systemId: string
  clientScope: string[]
  reuseScope: string
}

interface Props {
  onSubmit: (values: SectionAValues) => void
  onCancel: () => void
  isSubmitting: boolean
  error?: string
}

const inputClass = 'px-3.5 py-2.5 rounded-[10px] border border-atlas-border outline-none focus:border-atlas-accent text-sm w-full'
const labelClass = 'text-[12px] font-medium text-atlas-body'

export function RecordingFormSectionA({ onSubmit, onCancel, isSubmitting, error }: Props) {
  const { data: systems } = useSystems()
  const { data: clients } = useClients()

  const [targetUrl, setTargetUrl] = useState('')
  const [workflowName, setWorkflowName] = useState('')
  const [workflowNameTouched, setWorkflowNameTouched] = useState(false)
  const [lob, setLob] = useState<Lob>('AR')
  const [category, setCategory] = useState('')
  const [action, setAction] = useState('')
  const [systemId, setSystemId] = useState('')
  const [clientScope, setClientScope] = useState<string[]>([])
  const [reuseScope, setReuseScope] = useState('')

  const systemName = systems?.find((s) => s.id === systemId)?.name ?? null

  useEffect(() => {
    if (!workflowNameTouched) setWorkflowName(suggestWorkflowName(action || null, systemName))
  }, [action, systemName, workflowNameTouched])

  function selectLob(next: Lob) {
    setLob(next)
    setAction('') // action options depend on lob — a stale selection from another tab would be invalid
  }

  function toggleClient(id: string) {
    setClientScope((prev) => {
      if (id === GENERIC_CLIENT_SENTINEL) return prev.includes(id) ? [] : [id]
      const withoutGeneric = prev.filter((c) => c !== GENERIC_CLIENT_SENTINEL)
      return withoutGeneric.includes(id) ? withoutGeneric.filter((c) => c !== id) : [...withoutGeneric, id]
    })
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
          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>Target portal URL</label>
            <input
              required type="url" placeholder="https://portal.example.com/..."
              value={targetUrl} onChange={(e) => setTargetUrl(e.target.value)} className={inputClass}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>Category</label>
            <select value={category} onChange={(e) => setCategory(e.target.value)} className={inputClass}>
              <option value="">Select…</option>
              {CATEGORY_OPTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>Action</label>
            <select value={action} onChange={(e) => setAction(e.target.value)} className={inputClass}>
              <option value="">Select…</option>
              {ACTION_OPTIONS[lob].map((o) => <option key={o.value} value={o.value}>{o.value}</option>)}
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>System recorded in</label>
            <select value={systemId} onChange={(e) => setSystemId(e.target.value)} className={inputClass}>
              <option value="">Select…</option>
              {(systems ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>Applies to</label>
            <div className="flex flex-col gap-1 border border-atlas-border rounded-[10px] p-2 max-h-32 overflow-y-auto">
              <label className="flex items-center gap-2 text-[13px] px-1.5 py-1">
                <input
                  type="checkbox" checked={clientScope.includes(GENERIC_CLIENT_SENTINEL)}
                  onChange={() => toggleClient(GENERIC_CLIENT_SENTINEL)}
                />
                Generic (all clients)
              </label>
              {(clients ?? []).map((c) => (
                <label key={c.id} className="flex items-center gap-2 text-[13px] px-1.5 py-1">
                  <input
                    type="checkbox" checked={clientScope.includes(c.id)}
                    disabled={clientScope.includes(GENERIC_CLIENT_SENTINEL)}
                    onChange={() => toggleClient(c.id)}
                  />
                  {c.name}
                </label>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>Reuse scope</label>
            <select value={reuseScope} onChange={(e) => setReuseScope(e.target.value)} className={inputClass}>
              <option value="">Select…</option>
              {REUSE_SCOPE_OPTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>Title</label>
            <input
              value={workflowName}
              onChange={(e) => { setWorkflowName(e.target.value); setWorkflowNameTouched(true) }}
              className={inputClass}
            />
          </div>

          {error && <div className="text-[13px] text-atlas-diffOldText">{error}</div>}

          <button
            disabled={!canSubmit || isSubmitting}
            onClick={() => {
              const effectiveTitle = workflowName.trim() || suggestWorkflowName(action || null, systemName) || targetUrl.trim()
              onSubmit({ targetUrl: targetUrl.trim(), workflowName: effectiveTitle, lob, category, action, systemId, clientScope, reuseScope })
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
