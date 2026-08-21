// Recording form v2 — LOB-specific master lists for Client, Action, EHR.
//
// The form now shows two LOB tabs (AR, EV). Each tab has its own set of
// clients, actions, and EHR systems — all hardcoded here rather than
// fetched from the master-data API, since they're static operational
// vocabulary that rarely changes.

export type Lob = 'AR' | 'EV'

export const LOB_TABS: { lob: Lob; label: string }[] = [
  { lob: 'AR', label: 'AR' },
  { lob: 'EV', label: 'EV' },
]

// ---------------------------------------------------------------------------
// Client master lists — per LOB
// ---------------------------------------------------------------------------

export const CLIENT_OPTIONS: Record<Lob, string[]> = {
  AR: [
    'Audentes CSSL CSSC THSC',
    'Audentes CPI',
    'Audentes CSSA',
    'Audentes XRS',
    'Houston Heart Health',
    'Maiden Lane Medical',
    'MRI',
    'Pediatric Associates at Ridge',
    'Parkside Psychiatric Hospital Clinic',
    'Penta',
    'Tricity',
    'Voyage Health',
    'Urgent Care',
    'NHS',
    'PRMC',
  ],
  EV: [
    'Audentes Verification',
    'Voyage sales Verification',
    'Voyage intake Verification',
    'Housten Heart',
    'Pediatrics Ridge',
    'MRI Memorial Verification',
    'MLM',
    'TUC',
    'STKS',
    'NHS',
    'Healing Biologics',
  ],
}

// ---------------------------------------------------------------------------
// Action master lists — per LOB
// ---------------------------------------------------------------------------

export const ACTION_OPTIONS: Record<Lob, string[]> = {
  AR: [
    'Report Extraction',
    'Claim Submission',
    'Claim Correction',
    'Claim Resubmission',
    'Claim Reconsideration',
    'Void Claim',
    'Replacement Claim',
    'Secondary/Tertiary Billing',
    'Claim Rejection',
    'Denial Review',
    'Denial Correction',
    'Denial Resubmission',
    'Denial Appeal',
    'Appeal',
    'Peer-to-Peer Review',
    'Medical Necessity Appeal',
    'Coding Appeal',
    'Authorization Appeal',
    'Claim Status Follow-up',
    'Payer Call',
    'Payer Portal Follow-up',
    'Pending Claim Follow-up',
    'Medical Records Request',
    'Documentation Submission',
    'Payer Escalation',
    'Payment Posting',
    'Underpayment Review',
    'Underpayment Appeal',
    'Contractual Adjustment',
    'Payment Variance Review',
    'Recoupment Review',
    'Refund Processing',
    'Eligibility Verification',
    'Insurance Discovery',
    'Insurance Update',
    'COB Update',
    'Payer Correction',
    'Subscriber Information Correction',
    'Authorization Verification',
    'Patient Balance Review',
    'Patient Statement Generation',
    'Patient Billing',
    'Patient Payment Follow-up',
    'Payment Plan Setup',
    'Financial Assistance Review',
    'Patient Responsibility Adjustment',
    'Bad Debt Transfer',
    'Write-off',
    'Small Balance Write-off',
    'Administrative Adjustment',
    'Charity Care Adjustment',
    'Bad Debt',
    'Account Closure',
    'Zero Balance Resolution',
    'Duplicate Account/Claim Resolution',
    'Credit Balance Resolution',
  ],
  EV: [
    'Portal Verification',
    'Note Posting',
    'Report Extraction',
  ],
}

// ---------------------------------------------------------------------------
// EHR master lists — per LOB
// ---------------------------------------------------------------------------

export const EHR_OPTIONS: Record<Lob, string[]> = {
  AR: [
    'HST',
    'eCW',
    'EXA',
    'Netsmart',
    'Modmed',
    'Brightree',
    'Experity',
    'Cerner',
  ],
  EV: [
    'eCW',
    'Brightree',
    'Exa',
    'Experity',
    'Cerner',
    'Healing Biologix',
  ],
}

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
