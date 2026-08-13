import { describe, expect, it } from 'vitest'
import { PHASE_ORDER, phaseIndex } from './AuthoringPhaseTracker'

describe('phaseIndex', () => {
  it('returns the position of each phase in PHASE_ORDER', () => {
    expect(phaseIndex('replay')).toBe(0)
    expect(phaseIndex('brain')).toBe(2)
    expect(phaseIndex('done')).toBe(PHASE_ORDER.length - 1)
  })

  it('returns -1 for an unknown phase', () => {
    expect(phaseIndex('not-a-real-phase')).toBe(-1)
  })
})
