import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StepsViewer } from './StepsViewer'
import type { RecordingStep } from '../lib/recordings'

const STEPS: RecordingStep[] = [
  { order_index: 0, caption: 'Click #login-btn', offset_ms: 500 },
  { order_index: 1, caption: 'Fill #username', offset_ms: 1200 },
  { order_index: 2, caption: null, offset_ms: null },
]

describe('StepsViewer', () => {
  it('renders each step with its caption and offset', () => {
    render(<StepsViewer steps={STEPS} />)
    expect(screen.getByText('#0')).toBeInTheDocument()
    expect(screen.getByText('Click #login-btn')).toBeInTheDocument()
    expect(screen.getByText('Fill #username')).toBeInTheDocument()
    expect(screen.getByText('0.5s')).toBeInTheDocument()
    expect(screen.getByText('1.2s')).toBeInTheDocument()
  })

  it('shows the step count', () => {
    render(<StepsViewer steps={STEPS} />)
    expect(screen.getByText('(3)')).toBeInTheDocument()
  })

  it('renders an empty state when there are no steps', () => {
    render(<StepsViewer steps={[]} />)
    expect(screen.getByText('No steps recorded.')).toBeInTheDocument()
  })
})