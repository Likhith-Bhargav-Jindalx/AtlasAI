import { useState } from 'react'
import {
  EVIDENCE_REQUIRED_OPTIONS, PAYER_SCOPE_OPTIONS, RESOLUTION_PATH_OPTIONS,
  sectionBVisibility, type Lob,
} from '../lib/recordingFormSchema'
import { useCarcCodes, usePayers, useSystems } from '../lib/masterData'
import type { UpdateRecordingDetailsBody } from '../lib/recordings'

export interface SectionBValues extends UpdateRecordingDetailsBody {}

// The API returns already-saved values as `| null` (RecordingDetail);
// what gets sent back on submit is `| undefined` (UpdateRecordingDetailsBody,
// a PATCH body where an absent field means "don't touch this"). `initial`
// accepts either shape so a RecordingDetail can be passed straight through
// without the caller re-mapping null -> undefined field by field.
type NullableSectionBValues = { [K in keyof SectionBValues]?: SectionBValues[K] | null }

interface Props {
  lob: Lob
  action: string | null
  initial: NullableSectionBValues
  onSubmit: (values: SectionBValues) => void
  isSubmitting: boolean
  error?: string
}

const inputClass = 'px-3.5 py-2.5 rounded-[10px] border border-atlas-border outline-none focus:border-atlas-accent text-sm w-full'
const labelClass = 'text-[12px] font-medium text-atlas-body'

function toggleInArray(arr: string[], value: string): string[] {
  return arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value]
}

export function RecordingFormSectionB({ lob, action, initial, onSubmit, isSubmitting, error }: Props) {
  const { data: payers } = usePayers()
  const { data: systems } = useSystems()
  const { data: carcCodes } = useCarcCodes()

  const [payerScope, setPayerScope] = useState(initial.payer_scope ?? '')
  const [payerIds, setPayerIds] = useState<string[]>(initial.payer_ids ?? [])
  const [otherSystemIds, setOtherSystemIds] = useState<string[]>(initial.other_system_ids ?? [])
  const [carcCodeId, setCarcCodeId] = useState(initial.carc_code_id ?? '')
  const [codeFreeText, setCodeFreeText] = useState(initial.code_free_text ?? '')
  const [subReason, setSubReason] = useState(initial.sub_reason ?? '')
  const [resolutionPath, setResolutionPath] = useState<string[]>(initial.resolution_path ?? [])
  const [evidenceRequired, setEvidenceRequired] = useState<string[]>(initial.evidence_required ?? [])
  const [reportId, setReportId] = useState(initial.report_id ?? '')
  const [dateParamLogic, setDateParamLogic] = useState(initial.date_param_logic ?? '')
  const [outputDestination, setOutputDestination] = useState(initial.output_destination ?? '')
  const [postProcessing, setPostProcessing] = useState(initial.post_processing ?? '')
  const [scenarioDescription, setScenarioDescription] = useState(initial.scenario_description ?? '')

  const ctx = { lob, action, payerScope: payerScope || null, resolutionPath }
  const selectedCarcCode = carcCodes?.find((c) => c.id === carcCodeId)
  const showSubReason = sectionBVisibility.code(ctx) && (selectedCarcCode?.is_multi_use ?? false)

  function handleSubmit() {
    onSubmit({
      payer_scope: payerScope || undefined,
      payer_ids: payerIds.length ? payerIds : undefined,
      other_system_ids: otherSystemIds.length ? otherSystemIds : undefined,
      carc_code_id: carcCodeId || undefined,
      code_free_text: codeFreeText || undefined,
      sub_reason: subReason || undefined,
      resolution_path: resolutionPath.length ? resolutionPath : undefined,
      evidence_required: evidenceRequired.length ? evidenceRequired : undefined,
      report_id: reportId || undefined,
      date_param_logic: dateParamLogic || undefined,
      output_destination: outputDestination || undefined,
      post_processing: postProcessing || undefined,
      scenario_description: scenarioDescription || undefined,
    })
  }

  return (
    <div className="bg-atlas-card border border-atlas-border rounded-2xl p-5 flex flex-col gap-4 max-w-lg">
      <div className="font-display text-base font-semibold tracking-tight">Context</div>

      {sectionBVisibility.payerScope(ctx) && (
        <div className="flex flex-col gap-1.5">
          <label className={labelClass}>Payer scope</label>
          <select value={payerScope} onChange={(e) => setPayerScope(e.target.value)} className={inputClass}>
            <option value="">Select…</option>
            {PAYER_SCOPE_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </div>
      )}

      {sectionBVisibility.payers(ctx) && (
        <div className="flex flex-col gap-1.5">
          <label className={labelClass}>Payer(s) covered</label>
          <div className="flex flex-col gap-1 border border-atlas-border rounded-[10px] p-2 max-h-32 overflow-y-auto">
            {(payers ?? []).map((p) => (
              <label key={p.id} className="flex items-center gap-2 text-[13px] px-1.5 py-1">
                <input type="checkbox" checked={payerIds.includes(p.id)} onChange={() => setPayerIds(toggleInArray(payerIds, p.id))} />
                {p.name}
              </label>
            ))}
          </div>
        </div>
      )}

      {sectionBVisibility.otherSystems(ctx) && (
        <div className="flex flex-col gap-1.5">
          <label className={labelClass}>Other systems touched</label>
          <div className="flex flex-col gap-1 border border-atlas-border rounded-[10px] p-2 max-h-32 overflow-y-auto">
            {(systems ?? []).map((s) => (
              <label key={s.id} className="flex items-center gap-2 text-[13px] px-1.5 py-1">
                <input
                  type="checkbox" checked={otherSystemIds.includes(s.id)}
                  onChange={() => setOtherSystemIds(toggleInArray(otherSystemIds, s.id))}
                />
                {s.name}
              </label>
            ))}
          </div>
        </div>
      )}

      {sectionBVisibility.code(ctx) && (
        <div className="flex flex-col gap-1.5">
          <label className={labelClass}>Denial / rejection code</label>
          <select value={carcCodeId} onChange={(e) => setCarcCodeId(e.target.value)} className={inputClass}>
            <option value="">Select…</option>
            {(carcCodes ?? []).map((c) => <option key={c.id} value={c.id}>{c.code}{c.description ? ` — ${c.description}` : ''}</option>)}
          </select>
          <input
            placeholder="Or free text (e.g. clearinghouse rejection)"
            value={codeFreeText} onChange={(e) => setCodeFreeText(e.target.value)} className={inputClass}
          />
        </div>
      )}

      {showSubReason && (
        <div className="flex flex-col gap-1.5">
          <label className={labelClass}>Sub-reason</label>
          <select value={subReason} onChange={(e) => setSubReason(e.target.value)} className={inputClass}>
            <option value="">Select…</option>
            {['Missing NDC', 'Invalid taxonomy', 'Missing CLIA', 'Missing provider details', 'Missing description', 'Invalid group practice', 'Other'].map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>
        </div>
      )}

      {sectionBVisibility.resolutionPath(ctx) && (
        <div className="flex flex-col gap-1.5">
          <label className={labelClass}>Resolution path</label>
          <div className="flex flex-col gap-1 border border-atlas-border rounded-[10px] p-2">
            {RESOLUTION_PATH_OPTIONS.map((o) => (
              <label key={o} className="flex items-center gap-2 text-[13px] px-1.5 py-1">
                <input type="checkbox" checked={resolutionPath.includes(o)} onChange={() => setResolutionPath(toggleInArray(resolutionPath, o))} />
                {o}
              </label>
            ))}
          </div>
        </div>
      )}

      {sectionBVisibility.evidenceRequired(ctx) && (
        <div className="flex flex-col gap-1.5">
          <label className={labelClass}>Documents needed</label>
          <div className="flex flex-col gap-1 border border-atlas-border rounded-[10px] p-2">
            {EVIDENCE_REQUIRED_OPTIONS.map((o) => (
              <label key={o} className="flex items-center gap-2 text-[13px] px-1.5 py-1">
                <input type="checkbox" checked={evidenceRequired.includes(o)} onChange={() => setEvidenceRequired(toggleInArray(evidenceRequired, o))} />
                {o}
              </label>
            ))}
          </div>
        </div>
      )}

      {sectionBVisibility.reportFields(ctx) && (
        <>
          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>Report / file name in source system</label>
            <input value={reportId} onChange={(e) => setReportId(e.target.value)} className={inputClass} />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>Date range, relative to run date</label>
            <input
              placeholder='e.g. "prior calendar month" — not an absolute date'
              value={dateParamLogic} onChange={(e) => setDateParamLogic(e.target.value)} className={inputClass}
            />
          </div>
        </>
      )}

      {sectionBVisibility.reportingAdminFields(ctx) && (
        <>
          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>Where the output lands</label>
            <input
              placeholder="Path / SFTP / distribution list"
              value={outputDestination} onChange={(e) => setOutputDestination(e.target.value)} className={inputClass}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className={labelClass}>Manual work after download</label>
            <input
              placeholder='Explicit "None" if there is no manual work'
              value={postProcessing} onChange={(e) => setPostProcessing(e.target.value)} className={inputClass}
            />
          </div>
        </>
      )}

      <div className="flex flex-col gap-1.5">
        <label className={labelClass}>Scenario description</label>
        <textarea
          placeholder="One or two lines"
          value={scenarioDescription} onChange={(e) => setScenarioDescription(e.target.value)} className={inputClass}
        />
      </div>

      {error && <div className="text-[13px] text-atlas-diffOldText">{error}</div>}

      <button
        onClick={handleSubmit}
        disabled={isSubmitting}
        className="bg-atlas-primary hover:bg-atlas-primaryHover text-white rounded-full py-2.5 text-[13.5px] font-medium disabled:opacity-60"
      >
        {isSubmitting ? 'Saving…' : 'Save context'}
      </button>
    </div>
  )
}
