import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { FormOptionsPage } from './FormOptionsPage'

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

let options = [{ id: '1', label: 'Claim Submission', active: true }]

beforeEach(() => {
  options = [{ id: '1', label: 'Claim Submission', active: true }]
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/form-options/all?lob=AR&category=action') return Promise.resolve(jsonResponse(options))
    if (path === '/form-options' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string)
      options = [...options, { id: '2', label: body.label, active: true }]
      return Promise.resolve(jsonResponse({ id: '2', label: body.label, active: true }))
    }
    if (path === '/form-options/1' && init?.method === 'PATCH') {
      options = options.map((o) => (o.id === '1' ? { ...o, active: false } : o))
      return Promise.resolve(jsonResponse({ id: '1', label: 'Claim Submission', active: false }))
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <FormOptionsPage />
    </QueryClientProvider>,
  )
}

describe('FormOptionsPage', () => {
  it('lists existing options for the default lob/category', async () => {
    renderPage()
    expect(await screen.findByText('Claim Submission')).toBeInTheDocument()
  })

  it('adds a new option', async () => {
    renderPage()
    await screen.findByText('Claim Submission')
    fireEvent.change(screen.getByPlaceholderText('New option label'), { target: { value: 'New Action' } })
    fireEvent.click(screen.getByText('Add'))
    await waitFor(() => expect(screen.getByText('New Action')).toBeInTheDocument())
  })

  it('deactivates an option', async () => {
    renderPage()
    await screen.findByText('Claim Submission')
    fireEvent.click(screen.getByLabelText('Deactivate Claim Submission'))
    await waitFor(() => expect(screen.getByLabelText('Deactivate Claim Submission')).not.toBeChecked())
  })
})
