import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PipelineTimeline } from './PipelineTimeline'
import type { AuthoringRunDetail } from '../lib/authoring'

const RUN: AuthoringRunDetail = {
  id: 'run-1',
  status: 'running',
  current_phase: 'brain',
  repair_attempt: 0,
  events: [],
  golden_script: null,
  certification_runs: [],
}

describe('PipelineTimeline', () => {
  it('marks earlier phases done and the current phase running', () => {
    render(<PipelineTimeline run={RUN} />)
    expect(screen.getByText('Replay')).toBeInTheDocument()
    expect(screen.getByText('Extract')).toBeInTheDocument()
    expect(screen.getByText('Brain')).toBeInTheDocument()
    expect(screen.getByText('Golden')).toBeInTheDocument()
    expect(screen.getByText('Certifying')).toBeInTheDocument()
    expect(screen.getByText('Done')).toBeInTheDocument()
    expect(screen.getAllByText('done').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('running')).toBeInTheDocument()
  })

  it('marks the failing phase as failed for a failed run', () => {
    render(<PipelineTimeline run={{ ...RUN, status: 'failed', current_phase: 'golden' }} />)
    expect(screen.getByText('failed')).toBeInTheDocument()
    expect(screen.queryByText('running')).toBeNull()
  })

  it('shows the repair attempt counter', () => {
    render(<PipelineTimeline run={{ ...RUN, repair_attempt: 1 }} />)
    expect(screen.getByText(/repair attempt 1\/2/)).toBeInTheDocument()
  })
})