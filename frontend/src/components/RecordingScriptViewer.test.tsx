import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { RecordingScriptViewer } from './RecordingScriptViewer'

describe('RecordingScriptViewer', () => {
  it('renders the recorded script text', () => {
    render(<RecordingScriptViewer script={'page.goto("https://x")\npage.click("#go")\n'} />)
    expect(screen.getByText(/page\.goto\("https:\/\/x"\)/)).toBeInTheDocument()
    expect(screen.getByText(/page\.click\("#go"\)/)).toBeInTheDocument()
  })

  it('renders an empty state when there is no script', () => {
    render(<RecordingScriptViewer script={null} />)
    expect(screen.getByText(/no recorded script/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /download/i })).toBeNull()
  })

  it('copies the script to the clipboard', () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    render(<RecordingScriptViewer script={'page.click("#go")'} />)
    fireEvent.click(screen.getByRole('button', { name: /copy/i }))
    expect(writeText).toHaveBeenCalledWith('page.click("#go")')
  })

  it('downloads the script as a .py file', () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    render(<RecordingScriptViewer script={'page.click("#go")'} />)
    fireEvent.click(screen.getByRole('button', { name: /download/i }))
    const anchor = clickSpy.mock.instances[0] as HTMLAnchorElement
    expect(anchor.download).toBe('recording.py')
    expect(anchor.href).toMatch(/^blob:/)
  })
})