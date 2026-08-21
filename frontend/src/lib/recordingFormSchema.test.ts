import { describe, expect, it } from 'vitest'
import { sectionBVisibility, suggestWorkflowName } from './recordingFormSchema'

describe('sectionBVisibility.payerScope', () => {
  it('shows for AR and EV', () => {
    expect(sectionBVisibility.payerScope({ lob: 'AR', action: null, payerScope: null, resolutionPath: [] })).toBe(true)
    expect(sectionBVisibility.payerScope({ lob: 'EV', action: null, payerScope: null, resolutionPath: [] })).toBe(true)
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
  it('shows for denial-like actions only', () => {
    expect(sectionBVisibility.code({ lob: 'AR', action: 'Denial Review', payerScope: null, resolutionPath: [] })).toBe(true)
    expect(sectionBVisibility.code({ lob: 'AR', action: 'Claim Rejection', payerScope: null, resolutionPath: [] })).toBe(true)
    expect(sectionBVisibility.code({ lob: 'AR', action: 'Claim Submission', payerScope: null, resolutionPath: [] })).toBe(false)
  })
})

describe('sectionBVisibility.evidenceRequired', () => {
  it('shows only when resolution_path includes Appeal or Corrected claim', () => {
    expect(sectionBVisibility.evidenceRequired({ lob: 'AR', action: 'Denial Review', payerScope: null, resolutionPath: ['Appeal'] })).toBe(true)
    expect(sectionBVisibility.evidenceRequired({ lob: 'AR', action: 'Denial Review', payerScope: null, resolutionPath: ['Payer call'] })).toBe(false)
  })
})

describe('sectionBVisibility.reportFields', () => {
  it('shows for Report Extraction action', () => {
    expect(sectionBVisibility.reportFields({ lob: 'AR', action: 'Report Extraction', payerScope: null, resolutionPath: [] })).toBe(true)
  })

  it('hides for non-report actions', () => {
    expect(sectionBVisibility.reportFields({ lob: 'AR', action: 'Claim Submission', payerScope: null, resolutionPath: [] })).toBe(false)
  })
})

describe('suggestWorkflowName', () => {
  it('builds "Client + Action + EHR"', () => {
    expect(suggestWorkflowName('Audentes CPI', 'Claim Submission', 'eCW')).toBe('Audentes CPI + Claim Submission + eCW')
  })

  it('builds partial title when only some fields are set', () => {
    expect(suggestWorkflowName('Audentes CPI', null, null)).toBe('Audentes CPI')
    expect(suggestWorkflowName('Audentes CPI', 'Claim Submission', null)).toBe('Audentes CPI + Claim Submission')
  })

  it('is empty when nothing is chosen', () => {
    expect(suggestWorkflowName(null, null, null)).toBe('')
  })
})
