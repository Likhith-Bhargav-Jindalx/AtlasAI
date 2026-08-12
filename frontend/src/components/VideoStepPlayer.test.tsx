import { describe, expect, it } from 'vitest'
import { activeStepIndex, nextStepIndex } from './VideoStepPlayer'

describe('activeStepIndex', () => {
  const steps = [
    { order_index: 0, caption: 'a', offset_ms: 0 },
    { order_index: 1, caption: 'b', offset_ms: 5000 },
    { order_index: 2, caption: 'c', offset_ms: 12000 },
  ]

  it('picks the last step whose offset has passed', () => {
    expect(activeStepIndex(steps, 0)).toBe(0)
    expect(activeStepIndex(steps, 4.9)).toBe(0)
    expect(activeStepIndex(steps, 5)).toBe(1)
    expect(activeStepIndex(steps, 20)).toBe(2)
  })

  it('returns -1 for no steps', () => {
    expect(activeStepIndex([], 5)).toBe(-1)
  })
})

describe('nextStepIndex', () => {
  const steps = [
    { order_index: 0, caption: 'a', offset_ms: 0 },
    { order_index: 1, caption: 'b', offset_ms: 5000 },
    { order_index: 2, caption: 'c', offset_ms: 12000 },
  ]

  it('moves forward and backward, clamped to the ends', () => {
    expect(nextStepIndex(steps, 0, 'next')).toBe(1)
    expect(nextStepIndex(steps, 1, 'next')).toBe(2)
    expect(nextStepIndex(steps, 2, 'next')).toBe(2) // already last
    expect(nextStepIndex(steps, 1, 'prev')).toBe(0)
    expect(nextStepIndex(steps, 0, 'prev')).toBe(0) // already first
  })

  it('returns -1 for no steps', () => {
    expect(nextStepIndex([], 0, 'next')).toBe(-1)
  })
})
