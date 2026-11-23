/** Neutral wait while AuthProvider restores the session. No portal chrome. */
export function AuthLoading() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-fog px-6 text-text-muted">
      <p>Loading…</p>
    </main>
  )
}
