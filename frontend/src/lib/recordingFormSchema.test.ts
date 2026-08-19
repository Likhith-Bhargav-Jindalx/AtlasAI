import { describe, expect, it } from 'vitest'
import { sectionBVisibility, suggestWorkflowName } from './recordingFormSchema'

describe('sectionBVisibility.payerScope', () => {
  it('shows for AR and EV', () => {
    expect(sectionBVisibility.payerScope({ lob: 'AR', action: null, payerScope: null, resolutionPath: [] })).toBe(true)
    expect(sectionBVisibility.payerScope({ lob: 'EV', action: null, payerScope: null, resolutionPath: [] })).toBe(true)
  })

  it('shows for Generic only when the action came from PA or PP, not Reporting-Admin', () => {
    expect(sectionBVisibility.payerScope({ lob: 'Generic', action: 'Auth Submission', payerScope: null, resolutionPath: [] })).toBe(true)
    expect(sectionBVisibility.payerScope({ lob: 'Generic', action: 'ERA Posting', payerScope: null, resolutionPath: [] })).toBe(true)
    expect(sectionBVisibility.payerScope({ lob: 'Generic', action: 'Report Download', payerScope: null, resolutionPath: [] })).toBe(false)
  })
})

describe('sectionBVisibility.payers', () => {
  it('hides when payer_scope is All payers', () => {
    expect(sectionBVisibility.payers({ lob: 'AR', action: null, payerScope: 'All payers', resolutionPath: [] })).toBe(false)
  })

  it('shows when payer_scope is set to something else', () => {
    expect(sectionBVisibility.payers({ lob: 'AR', action: null, payerScope: 'Payer-specific', resolutionPath: [] })).toBe(true)
  })

  it('hides when payer_scope has not been chosen yet', () => {
    expect(sectionBVisibility.payers({ lob: 'AR', action: null, payerScope: null, resolutionPath: [] })).toBe(false)
  })
})

describe('sectionBVisibility.code', () => {
  it('shows for Denial/Rejection Check/Denial Posting only', () => {
    expect(sectionBVisibility.code({ lob: 'AR', action: 'Denial', payerScope: null, resolutionPath: [] })).toBe(true)
    expect(sectionBVisibility.code({ lob: 'AR', action: 'Rejection Check', payerScope: null, resolutionPath: [] })).toBe(true)
    expect(sectionBVisibility.code({ lob: 'Generic', action: 'Denial Posting', payerScope: null, resolutionPath: [] })).toBe(true)
    expect(sectionBVisibility.code({ lob: 'AR', action: 'Status Check', payerScope: null, resolutionPath: [] })).toBe(false)
  })
})

describe('sectionBVisibility.evidenceRequired', () => {
  it('shows only when resolution_path includes Appeal or Corrected claim', () => {
    expect(sectionBVisibility.evidenceRequired({ lob: 'AR', action: 'Denial', payerScope: null, resolutionPath: ['Appeal'] })).toBe(true)
    expect(sectionBVisibility.evidenceRequired({ lob: 'AR', action: 'Denial', payerScope: null, resolutionPath: ['Payer call'] })).toBe(false)
  })
})

describe('sectionBVisibility.reportFields / reportingAdminFields', () => {
  it('reportFields shows for Report Download/Generation regardless of lob', () => {
    expect(sectionBVisibility.reportFields({ lob: 'Generic', action: 'Report Download', payerScope: null, resolutionPath: [] })).toBe(true)
  })

  it('reportingAdminFields shows only for the Reporting-Admin-sourced Generic actions', () => {
    expect(sectionBVisibility.reportingAdminFields({ lob: 'Generic', action: 'Report Download', payerScope: null, resolutionPath: [] })).toBe(true)
    expect(sectionBVisibility.reportingAdminFields({ lob: 'Generic', action: 'ERA Posting', payerScope: null, resolutionPath: [] })).toBe(false)
    expect(sectionBVisibility.reportingAdminFields({ lob: 'AR', action: 'Status Check', payerScope: null, resolutionPath: [] })).toBe(false)
  })
})

describe('suggestWorkflowName', () => {
  it('builds "ACTION – SYSTEM – v1"', () => {
    expect(suggestWorkflowName('Status Check', 'Availity')).toBe('Status Check – Availity – v1')
  })

  it('is empty until both action and system are chosen', () => {
    expect(suggestWorkflowName(null, 'Availity')).toBe('')
    expect(suggestWorkflowName('Status Check', null)).toBe('')
  })
})
