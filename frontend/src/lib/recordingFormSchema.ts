// Recording form v2 — LOB-specific Client/Action/EHR options.
//
// The form shows two LOB tabs (AR, EV). Client/Action/EHR options per LOB
// are database-backed (see `formOptions.ts`'s useFormOptions), not
// hardcoded here — they can be managed from the Form options admin page.

export type Lob = 'AR' | 'EV'

export const LOB_TABS: { lob: Lob; label: string }[] = [
  { lob: 'AR', label: 'AR' },
  { lob: 'EV', label: 'EV' },
]

// ---------------------------------------------------------------------------
// Specificity options — EHR or Payor
// ---------------------------------------------------------------------------

export const SPECIFICITY_OPTIONS = ['EHR', 'Payor'] as const

// ---------------------------------------------------------------------------
// Section B — kept from v1 for the review page (unchanged)
// ---------------------------------------------------------------------------

export const PAYER_SCOPE_OPTIONS = ['Payer-specific', 'Multi-payer portal', 'All payers'] as const
export const RESOLUTION_PATH_OPTIONS = [
  'Corrected claim', 'Appeal', 'Refile to correct payer', 'Payer call',
  'Write-off / adjustment', 'Bill to patient', 'Escalate internally', 'No action (wait)',
] as const
export const EVIDENCE_REQUIRED_OPTIONS = [
  'Medical records', 'Auth number', 'EOB', 'Proof of timely filing', 'W9', 'None',
] as const

export interface SectionBContext {
  lob: Lob
  action: string | null
  payerScope: string | null
  resolutionPath: string[]
}

/** Denial-like actions that need code/sub_reason follow-up. */
const DENIAL_LIKE_ACTIONS = new Set([
  'Denial Review', 'Denial Correction', 'Denial Resubmission', 'Denial Appeal',
  'Claim Rejection',
])
const RESOLUTION_PATH_ACTIONS = new Set([
  'Denial Review', 'Denial Correction', 'Denial Resubmission', 'Denial Appeal',
  'Underpayment Review', 'Underpayment Appeal',
])
const REPORT_ACTIONS = new Set(['Report Extraction'])

export const sectionBVisibility = {
  payerScope: (_ctx: SectionBContext) => true,
  payers: (ctx: SectionBContext) => ctx.payerScope !== null && ctx.payerScope !== 'All payers',
  otherSystems: (_ctx: SectionBContext) => true,
  code: (ctx: SectionBContext) => ctx.action !== null && DENIAL_LIKE_ACTIONS.has(ctx.action),
  resolutionPath: (ctx: SectionBContext) => ctx.action !== null && RESOLUTION_PATH_ACTIONS.has(ctx.action),
  evidenceRequired: (ctx: SectionBContext) =>
    ctx.resolutionPath.includes('Appeal') || ctx.resolutionPath.includes('Corrected claim'),
  reportFields: (ctx: SectionBContext) => ctx.action !== null && REPORT_ACTIONS.has(ctx.action),
  reportingAdminFields: (_ctx: SectionBContext) => false,
}

// ---------------------------------------------------------------------------
// Auto-title: Client + Action + EHR
// ---------------------------------------------------------------------------

export function suggestWorkflowName(
  client: string | null,
  action: string | null,
  ehr: string | null,
): string {
  const parts = [client, action, ehr].filter(Boolean)
  if (parts.length === 0) return ''
  return parts.join(' + ')
}
