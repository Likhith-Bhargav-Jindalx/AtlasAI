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
    if (path === '/form-options?lob=AR&category=action') {
      return Promise.resolve(jsonResponse([
        { id: '2', label: 'Claim Submission', active: true },
        { id: '4', label: 'Denial Correction', active: true },
      ]))
    }
    if (path === '/form-options?lob=AR&category=ehr') return Promise.resolve(jsonResponse([{ id: '3', label: 'eCW', active: true }]))
    if (path === '/subscenarios?action_id=2') return Promise.resolve(jsonResponse([]))
    if (path === '/subscenarios?action_id=4') return Promise.resolve(jsonResponse([{ id: '5', label: 'Bundled Denial', active: true }]))
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

function openDropdown(label: string) {
  const dropdownLabel = screen.getByText(label)
  const toggle = dropdownLabel.parentElement!.querySelector('button')!
  fireEvent.click(toggle)
}

describe('RecordingFormSectionA', () => {
  it('loads Client options from the API for the active LOB', async () => {
    renderForm()
    openDropdown('Select Client')
    await waitFor(() => expect(screen.getByText('Acme Clinic')).toBeInTheDocument())
  })

  it('does not show a subscenario field before an action is selected', () => {
    renderForm()
    expect(screen.queryByText('Select Subscenario')).toBeNull()
  })

  it('does not show a subscenario field for an action with none', async () => {
    renderForm()
    openDropdown('Select Action')
    fireEvent.click(await screen.findByText('Claim Submission'))
    await waitFor(() => expect(screen.queryByText('Select Subscenario')).toBeNull())
  })

  it('shows and loads the subscenario field for an action that has some', async () => {
    renderForm()
    openDropdown('Select Action')
    fireEvent.click(await screen.findByText('Denial Correction'))
    await waitFor(() => expect(screen.getByText('Select Subscenario')).toBeInTheDocument())
    openDropdown('Select Subscenario')
    await waitFor(() => expect(screen.getByText('Bundled Denial')).toBeInTheDocument())
  })
})
