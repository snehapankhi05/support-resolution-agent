import { useId, useState, type FormEvent } from 'react'

import { ApiError } from '../api'
import { useAuth } from '../auth'

function clientValidation(email: string, password: string): string | null {
  const trimmed = email.trim()
  if (!trimmed) return 'Email is required'
  if (!trimmed.includes('@') || trimmed.includes(' ')) return 'Enter a valid email'
  if (!password) return 'Password is required'
  return null
}

/**
 * Blueprint login: ink shell, paper form, AuthProvider.login → GuestOnly role redirect.
 */
export function LoginPage() {
  const { login, error: sessionError, status } = useAuth()
  const emailId = useId()
  const passwordId = useId()
  const errorId = useId()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const [ignoreSessionError, setIgnoreSessionError] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const displayError = localError ?? (ignoreSessionError ? null : sessionError)
  const busy = submitting || status === 'loading'

  function clearFormErrors() {
    setLocalError(null)
    setIgnoreSessionError(true)
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const validation = clientValidation(email, password)
    if (validation !== null) {
      setLocalError(validation)
      setIgnoreSessionError(true)
      return
    }

    setLocalError(null)
    setIgnoreSessionError(false)
    setSubmitting(true)
    try {
      await login({ email, password })
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Sign in failed'
      setLocalError(message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login-shell relative flex min-h-screen items-center justify-center px-6 py-16">
      <div className="login-wash pointer-events-none absolute inset-0" aria-hidden="true" />
      <section className="login-panel relative w-full max-w-md rounded-lg border border-line bg-paper p-8 text-text shadow-none">
        <header className="mb-8 text-center">
          <h1 className="font-brand text-3xl font-semibold tracking-tight text-ink">
            Support Resolution
          </h1>
          <p className="mt-2 text-sm text-text-muted">Human-approved refunds</p>
        </header>

        <form className="flex flex-col gap-5" onSubmit={onSubmit} noValidate>
          {displayError ? (
            <p
              id={errorId}
              role="alert"
              className="rounded-md border border-danger/20 bg-danger-soft px-3 py-2 text-sm text-danger"
            >
              {displayError}
            </p>
          ) : null}

          <div className="flex flex-col gap-1.5">
            <label htmlFor={emailId} className="text-sm font-medium text-text">
              Email
            </label>
            <input
              id={emailId}
              name="email"
              type="email"
              autoComplete="username"
              value={email}
              disabled={busy}
              aria-invalid={displayError !== null}
              aria-describedby={displayError ? errorId : undefined}
              onChange={(event) => {
                setEmail(event.target.value)
                clearFormErrors()
              }}
              className="rounded-md border border-line bg-paper px-3 py-2.5 text-text outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lamp disabled:opacity-60"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor={passwordId} className="text-sm font-medium text-text">
              Password
            </label>
            <input
              id={passwordId}
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              disabled={busy}
              aria-invalid={displayError !== null}
              onChange={(event) => {
                setPassword(event.target.value)
                clearFormErrors()
              }}
              className="rounded-md border border-line bg-paper px-3 py-2.5 text-text outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lamp disabled:opacity-60"
            />
          </div>

          <button
            type="submit"
            disabled={busy}
            aria-busy={busy}
            className="mt-1 rounded-md bg-accent px-4 py-2.5 text-sm font-semibold text-text-on-ink transition-opacity hover:bg-accent-hover disabled:opacity-60"
          >
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </section>
    </main>
  )
}
