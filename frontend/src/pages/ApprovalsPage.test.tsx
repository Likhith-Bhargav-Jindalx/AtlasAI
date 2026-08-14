import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../auth/AuthContext'
import { ApprovalsPage } from './ApprovalsPage'

const HEAL_EVENT = {
  id: 'heal-1', golden_script_id: 'gs-1', portal_domain: 'portal.example.com', trigger: 'on_demand',
  status: 'pending_approval', created_at: new Date().toISOString(),
  diff_summary: [{ step_index: 0, field: 'selector', old_value: '#a', new_value: '#b', before_screenshot: '/x/before.png', after_screenshot: '/x/after.png' }],
}

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'dev-1', org_id: 'org-1', email: 'd@x.com', role: 'member', job_function: 'developer' }))
    if (path.startsWith('/heal-events?')) return Promise.resolve(jsonResponse([HEAL_EVENT]))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {}, removeItem: () => {} })
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider><ApprovalsPage /></AuthProvider>
    </QueryClientProvider>,
  )
}

describe('ApprovalsPage screenshots', () => {
  it('renders before/after images when the diff entry has them', async () => {
    renderPage()
    const beforeImg = await screen.findByAltText(/before/i)
    const afterImg = await screen.findByAltText(/after/i)
    expect(beforeImg).toHaveAttribute('src', expect.stringContaining('/heal-events/heal-1/screenshots/0/before'))
    expect(afterImg).toHaveAttribute('src', expect.stringContaining('/heal-events/heal-1/screenshots/0/after'))
  })

  it('renders no images and shows text diff unchanged when both screenshots are null (backward compatibility)', async () => {
    const healEventNoScreenshots = {
      id: 'heal-2', golden_script_id: 'gs-2', portal_domain: 'portal.example.com', trigger: 'on_demand',
      status: 'pending_approval', created_at: new Date().toISOString(),
      diff_summary: [{ step_index: 0, field: 'selector', old_value: '#c', new_value: '#d', before_screenshot: null, after_screenshot: null }],
    }
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'dev-1', org_id: 'org-1', email: 'd@x.com', role: 'member', job_function: 'developer' }))
      if (path.startsWith('/heal-events?')) return Promise.resolve(jsonResponse([healEventNoScreenshots]))
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    renderPage()
    const images = screen.queryAllByRole('img')
    expect(images).toHaveLength(0)
    await screen.findByText('#c')
    await screen.findByText('#d')
  })

  it('renders only one image when only one of before/after is present', async () => {
    const healEventOneScreenshot = {
      id: 'heal-3', golden_script_id: 'gs-3', portal_domain: 'portal.example.com', trigger: 'on_demand',
      status: 'pending_approval', created_at: new Date().toISOString(),
      diff_summary: [{ step_index: 0, field: 'selector', old_value: '#e', new_value: '#f', before_screenshot: '/x/before.png', after_screenshot: null }],
    }
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace('http://localhost:8000', '')
      if (path === '/auth/me') return Promise.resolve(jsonResponse({ user_id: 'dev-1', org_id: 'org-1', email: 'd@x.com', role: 'member', job_function: 'developer' }))
      if (path.startsWith('/heal-events?')) return Promise.resolve(jsonResponse([healEventOneScreenshot]))
      return Promise.reject(new Error(`unexpected fetch: ${path}`))
    }))
    renderPage()
    const beforeImg = await screen.findByAltText(/before/i)
    expect(beforeImg).toBeInTheDocument()
    const images = screen.queryAllByRole('img')
    expect(images).toHaveLength(1)
    expect(beforeImg).toHaveAttribute('src', expect.stringContaining('/heal-events/heal-3/screenshots/0/before'))
  })
})
