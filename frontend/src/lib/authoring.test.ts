import { describe, expect, it } from 'vitest'
import { isTerminalStatus } from './authoring'

describe('isTerminalStatus', () => {
  it('treats completed, failed, and cancelled as terminal', () => {
    expect(isTerminalStatus('completed')).toBe(true)
    expect(isTerminalStatus('failed')).toBe(true)
    expect(isTerminalStatus('cancelled')).toBe(true)
  })

  it('treats running and waiting_for_developer as non-terminal', () => {
    expect(isTerminalStatus('running')).toBe(false)
    expect(isTerminalStatus('waiting_for_developer')).toBe(false)
  })
})
