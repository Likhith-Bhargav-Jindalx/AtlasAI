import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RecordingFormSectionA } from './RecordingFormSectionA'

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/form-options?lob=AR&category=client') return Promise.resolve(jsonResponse([{ id: '1', label: 'Acme Clinic', active: true }]))
    if (path === '/form-options?lob=AR&category=action') return Promise.resolve(jsonResponse([{ id: '2', label: 'Claim Submission', active: true }]))
    if (path === '/form-options?lob=AR&category=ehr') return Promise.resolve(jsonResponse([{ id: '3', label: 'eCW', active: true }]))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderForm() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <RecordingFormSectionA onSubmit={vi.fn()} onCancel={vi.fn()} isSubmitting={false} />
    </QueryClientProvider>,
  )
}

describe('RecordingFormSectionA', () => {
  it('loads Client options from the API for the active LOB', async () => {
    renderForm()
    const clientLabel = screen.getByText('Select Client')
    const clientToggle = clientLabel.parentElement!.querySelector('button')!
    fireEvent.click(clientToggle)
    await waitFor(() => expect(screen.getByText('Acme Clinic')).toBeInTheDocument())
  })
})
