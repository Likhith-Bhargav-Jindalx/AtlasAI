import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PageExtractionsList } from './PageExtractionsList'
import type { PageExtraction } from '../lib/extractions'

describe('PageExtractionsList', () => {
  it('renders one row per extraction with a download action', () => {
    const extractions: PageExtraction[] = [
      {
        id: '1',
        page_index: 0,
        url: 'https://portal.example.com/summary',
        data: { fields: { mrn: 'MRN-1' }, tables: [] },
        extracted_at: '2026-08-13T10:00:00Z',
      },
    ]
    render(<PageExtractionsList extractions={extractions} />)
    expect(screen.getByText(/summary/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /download/i })).toBeInTheDocument()
  })

  it('renders an empty state with no extractions', () => {
    render(<PageExtractionsList extractions={[]} />)
    expect(screen.getByText(/no page extractions/i)).toBeInTheDocument()
  })
})
