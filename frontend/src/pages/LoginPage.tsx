import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { ApiError } from '../lib/api'

export function LoginPage() {
  const { user, login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (user) return <Navigate to="/" replace />

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-atlas-bg">
      <form onSubmit={handleSubmit} className="w-full max-w-sm bg-atlas-card border border-atlas-border rounded-2xl p-8 flex flex-col gap-5">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-[9px] bg-atlas-primary text-white flex items-center justify-center text-sm font-bold">A</div>
          <div className="font-display text-lg font-semibold tracking-tight">Atlas AI</div>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-atlas-ink" htmlFor="email">Email</label>
          <input
            id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
            className="px-3.5 py-2.5 rounded-[10px] border border-atlas-border outline-none focus:border-atlas-accent text-[15px]"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-atlas-ink" htmlFor="password">Password</label>
          <input
            id="password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
            className="px-3.5 py-2.5 rounded-[10px] border border-atlas-border outline-none focus:border-atlas-accent text-[15px]"
          />
        </div>
        {error && <div className="text-sm text-atlas-diffOldText">{error}</div>}
        <button
          type="submit" disabled={submitting}
          className="mt-1 bg-atlas-primary hover:bg-atlas-primaryHover text-white rounded-full py-2.5 text-[13.5px] font-medium disabled:opacity-60"
        >
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
