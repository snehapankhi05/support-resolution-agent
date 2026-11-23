import { NavLink, Outlet } from 'react-router-dom'

import { useAuth } from '../auth'

function railClass({ isActive }: { isActive: boolean }) {
  return `flex items-center gap-2 rounded-md px-3 py-2.5 text-sm font-medium ${
    isActive
      ? 'rail-current text-text-on-ink'
      : 'text-text-on-ink/70 hover:bg-ink hover:text-text-on-ink'
  }`
}

function mobileClass({ isActive }: { isActive: boolean }) {
  return `flex flex-1 items-center justify-center gap-2 py-3 text-sm font-medium ${
    isActive ? 'rail-current text-text-on-ink' : 'text-text-on-ink/70'
  }`
}

/**
 * Ink reviewer chrome: left rail (desktop), bottom bar (mobile), paper stage.
 */
export function ReviewerShell() {
  const { user, logout } = useAuth()

  return (
    <div className="shell-enter flex min-h-screen bg-ink text-text-on-ink">
      <a href="#main" className="skip-link skip-link-dark">
        Skip to main content
      </a>

      <aside
        className="hidden w-60 shrink-0 flex-col border-r border-line-dark bg-ink-elevated md:flex"
        aria-label="Reviewer"
      >
        <div className="px-5 py-6">
          <p className="font-brand text-lg font-semibold tracking-tight">
            Support Resolution
          </p>
          <p className="mt-1 text-xs text-text-on-ink/50">Review console</p>
        </div>

        <nav className="flex flex-1 flex-col gap-1 px-3" aria-label="Reviewer">
          <NavLink to="/review" end className={railClass}>
            {({ isActive }) => (
              <>
                {isActive ? <span className="rail-tick" aria-hidden="true" /> : null}
                Overview
              </>
            )}
          </NavLink>
          <NavLink to="/review/queue" className={railClass}>
            {({ isActive }) => (
              <>
                {isActive ? <span className="rail-tick" aria-hidden="true" /> : null}
                Queue
              </>
            )}
          </NavLink>
        </nav>

        <div className="border-t border-line-dark px-5 py-4">
          <p className="truncate text-sm text-text-on-ink/80">
            {user?.name ?? 'Reviewer'}
          </p>
          <p className="truncate text-xs text-text-on-ink/45">{user?.role}</p>
          <button
            type="button"
            onClick={logout}
            className="mt-3 min-h-10 text-sm font-medium text-accent-hover hover:underline"
          >
            Log out
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-line-dark px-4 py-3 md:hidden">
          <p className="font-brand text-base font-semibold">Support Resolution</p>
          <button
            type="button"
            onClick={logout}
            className="min-h-10 px-2 text-sm text-accent-hover"
          >
            Log out
          </button>
        </header>

        <main
          id="main"
          className="flex-1 bg-fog p-4 text-text sm:p-6 md:rounded-tl-lg md:m-0"
        >
          <div className="mx-auto min-h-full max-w-5xl rounded-lg border border-line bg-paper p-6 shadow-none">
            <Outlet />
          </div>
        </main>

        <nav
          className="flex border-t border-line-dark bg-ink-elevated md:hidden"
          aria-label="Reviewer mobile"
        >
          <NavLink to="/review" end className={mobileClass}>
            {({ isActive }) => (
              <>
                {isActive ? <span className="rail-tick" aria-hidden="true" /> : null}
                Overview
              </>
            )}
          </NavLink>
          <NavLink to="/review/queue" className={mobileClass}>
            {({ isActive }) => (
              <>
                {isActive ? <span className="rail-tick" aria-hidden="true" /> : null}
                Queue
              </>
            )}
          </NavLink>
        </nav>
      </div>
    </div>
  )
}
