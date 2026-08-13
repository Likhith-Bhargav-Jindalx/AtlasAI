import { describe, expect, it } from 'vitest'
import { levelColorClass } from './AuthoringEventLog'

describe('levelColorClass', () => {
  it('returns a distinct class per level', () => {
    expect(levelColorClass('info')).toBe('text-atlas-body')
    expect(levelColorClass('warning')).toBe('text-atlas-muted')
    expect(levelColorClass('error')).toBe('text-atlas-diffOldText')
  })

  it('falls back to the info color for an unknown level', () => {
    expect(levelColorClass('not-a-real-level')).toBe('text-atlas-body')
  })
})
