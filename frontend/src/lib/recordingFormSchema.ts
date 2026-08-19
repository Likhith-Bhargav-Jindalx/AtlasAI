// Recording form v1 — see docs/superpowers/specs/2026-08-19-recording-form-v1-design.md
// and the source doc atlas-recording-form-v1.md (repo root).
//
// The "Generic" tab merges the doc's PA/PP/Reporting-Admin lobs into one —
// each Generic action remembers which of those three it came from
// (`sourceLob`) purely so Section B's show-when rules still match the
// doc's original per-lob conditions. That tag never leaves the frontend.

export type Lob = 'AR' | 'EV' | 'Generic'
export type SourceLob = 'AR' | 'EV' | 'PA' | 'PP' | 'Reporting-Admin'

export const LOB_TABS: { lob: Lob; label: string }[] = [
  { lob: 'AR', label: 'AR' },
  { lob: 'EV', label: 'EV' },
  { lob: 'Generic', label: 'Generic' },
]

export interface ActionOption {
  value: string
  sourceLob: SourceLob
}

export const ACTION_OPTIONS: Record<Lob, ActionOption[]> = {
  AR: [
    { value: 'Status Check', sourceLob: 'AR' },
    { value: 'Status Follow-up', sourceLob: 'AR' },
    { value: 'Denial', sourceLob: 'AR' },
    { value: 'Rejection Check', sourceLob: 'AR' },
    { value: 'Payment Research', sourceLob: 'AR' },
  ],
  EV: [
    { value: 'Portal Verification', sourceLob: 'EV' },
    { value: 'IVR Verification', sourceLob: 'EV' },
    { value: 'Rep Call', sourceLob: 'EV' },
    { value: 'Mismatch Resolution', sourceLob: 'EV' },
  ],
  Generic: [
    { value: 'Auth Submission', sourceLob: 'PA' },
    { value: 'Auth Follow-up', sourceLob: 'PA' },
    { value: 'Clinical Upload', sourceLob: 'PA' },
    { value: 'Auth Entry in PMS', sourceLob: 'PA' },
    { value: 'ERA Posting', sourceLob: 'PP' },
    { value: 'EOB Manual Posting', sourceLob: 'PP' },
    { value: 'Denial Posting', sourceLob: 'PP' },
    { value: 'Unapplied / Recoupment', sourceLob: 'PP' },
    { value: 'Report Download', sourceLob: 'Reporting-Admin' },
    { value: 'Report Generation', sourceLob: 'Reporting-Admin' },
    { value: 'File Transfer', sourceLob: 'Reporting-Admin' },
    { value: 'Master Data Update', sourceLob: 'Reporting-Admin' },
  ],
}

export const CATEGORY_OPTIONS = ['Process', 'Scenario'] as const
export const REUSE_SCOPE_OPTIONS = ['Client-specific', 'Generic'] as const
export const PAYER_SCOPE_OPTIONS = ['Payer-specific', 'Multi-payer portal', 'All payers'] as const
export const RESOLUTION_PATH_OPTIONS = [
  'Corrected claim', 'Appeal', 'Refile to correct payer', 'Payer call',
  'Write-off / adjustment', 'Bill to patient', 'Escalate internally', 'No action (wait)',
] as const
export const EVIDENCE_REQUIRED_OPTIONS = [
  'Medical records', 'Auth number', 'EOB', 'Proof of timely filing', 'W9', 'None',
] as const

export const GENERIC_CLIENT_SENTINEL = 'generic'

function sourceLobFor(lob: Lob, action: string | null): SourceLob | null {
  if (!action) return null
  return ACTION_OPTIONS[lob].find((o) => o.value === action)?.sourceLob ?? null
}

/** Section A's `action`-driven codes/rejections needing sub_reason follow-up. */
const DENIAL_LIKE_ACTIONS = new Set(['Denial', 'Rejection Check', 'Denial Posting'])
const RESOLUTION_PATH_ACTIONS = new Set(['Denial', 'Rejection Check', 'Payment Research'])
const REPORT_ACTIONS = new Set(['Report Download', 'Report Generation'])

export interface SectionBContext {
  lob: Lob
  action: string | null
  payerScope: string | null
  resolutionPath: string[]
}

/** True when the request's lob/action combination is one of the doc's
 * AR/EV/PA/PP flavors (as opposed to Reporting-Admin) — drives payer_scope/
 * payers/other_systems visibility exactly per the doc's original condition
 * `lob ∈ {AR, EV, PA, PP}`. */
function isPayerBearingFlow(ctx: SectionBContext): boolean {
  if (ctx.lob === 'AR' || ctx.lob === 'EV') return true
  const source = sourceLobFor(ctx.lob, ctx.action)
  return source === 'PA' || source === 'PP'
}

function isReportingAdminFlow(ctx: SectionBContext): boolean {
  return sourceLobFor(ctx.lob, ctx.action) === 'Reporting-Admin'
}

export const sectionBVisibility = {
  payerScope: (ctx: SectionBContext) => isPayerBearingFlow(ctx),
  payers: (ctx: SectionBContext) => isPayerBearingFlow(ctx) && ctx.payerScope !== null && ctx.payerScope !== 'All payers',
  otherSystems: (ctx: SectionBContext) => isPayerBearingFlow(ctx),
  code: (ctx: SectionBContext) => ctx.action !== null && DENIAL_LIKE_ACTIONS.has(ctx.action),
  // sub_reason's actual show-when also depends on whether the chosen CARC
  // code is multi-use — resolved by the caller (it has the master-data
  // list), not derivable from this static schema alone.
  resolutionPath: (ctx: SectionBContext) => ctx.action !== null && RESOLUTION_PATH_ACTIONS.has(ctx.action),
  evidenceRequired: (ctx: SectionBContext) =>
    ctx.resolutionPath.includes('Appeal') || ctx.resolutionPath.includes('Corrected claim'),
  reportFields: (ctx: SectionBContext) => ctx.action !== null && REPORT_ACTIONS.has(ctx.action),
  reportingAdminFields: (ctx: SectionBContext) => isReportingAdminFlow(ctx),
}

export function suggestWorkflowName(action: string | null, systemName: string | null): string {
  if (!action || !systemName) return ''
  return `${action} – ${systemName} – v1`
}
