import { describe, expect, it } from 'vitest'
import { formatDuration } from './format'

describe('formatDuration', () => {
  it('formats seconds as m:ss', () => {
    expect(formatDuration(192)).toBe('3:12')
    expect(formatDuration(5)).toBe('0:05')
  })

  it('renders a dash for null', () => {
    expect(formatDuration(null)).toBe('—')
  })
})
