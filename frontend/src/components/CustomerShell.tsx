import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { useAuth } from '../auth'

const navClass = ({ isActive }: { isActive: boolean }) =>
  `text-sm font-medium ${
    isActive
      ? 'text-accent underline decoration-lamp decoration-2 underline-offset-4'
      : 'text-text hover:text-accent'
  }`

/**
 * Light customer chrome: Orders, Cases list, and New case.
 */
export function CustomerShell() {
  const { user, logout } = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div className="shell-enter flex min-h-screen flex-col bg-fog text-text">
      <a href="#main" className="skip-link skip-link-light">
        Skip to main content
      </a>

      <header className="border-b border-line bg-paper">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <div className="flex min-w-0 items-center gap-6">
            <p className="font-brand truncate text-lg font-semibold tracking-tight text-ink sm:text-xl">
              Support Resolution
            </p>
            <nav
              className="hidden items-center gap-5 md:flex"
              aria-label="Customer"
            >
              <NavLink to="/orders" className={navClass}>
                Orders
              </NavLink>
              <NavLink to="/cases" className={navClass} end>
                Cases
              </NavLink>
              <NavLink to="/cases/new" className={navClass}>
                New case
              </NavLink>
            </nav>
          </div>

          <div className="flex items-center gap-3">
            <p className="hidden max-w-[12rem] truncate text-sm text-text-muted sm:block">
              {user?.name ?? user?.email}
            </p>
            <button
              type="button"
              onClick={logout}
              className="min-h-10 rounded-md px-3 py-2 text-sm font-medium text-accent hover:text-accent-hover"
            >
              Log out
            </button>
            <button
              type="button"
              className="min-h-10 rounded-md border border-line px-3 py-2 text-sm text-text md:hidden"
              aria-expanded={menuOpen}
              aria-controls="customer-mobile-nav"
              onClick={() => setMenuOpen((open) => !open)}
            >
              Menu
            </button>
          </div>
        </div>

        {menuOpen ? (
          <div
            id="customer-mobile-nav"
            className="border-t border-line px-4 py-3 md:hidden"
          >
            <ul className="flex flex-col gap-3">
              <li>
                <NavLink
                  to="/orders"
                  className={({ isActive }) =>
                    `text-sm font-medium ${isActive ? 'text-accent' : 'text-text'}`
                  }
                  onClick={() => setMenuOpen(false)}
                >
                  Orders
                </NavLink>
              </li>
              <li>
                <NavLink
                  to="/cases"
                  end
                  className={({ isActive }) =>
                    `text-sm font-medium ${isActive ? 'text-accent' : 'text-text'}`
                  }
                  onClick={() => setMenuOpen(false)}
                >
                  Cases
                </NavLink>
              </li>
              <li>
                <NavLink
                  to="/cases/new"
                  className={({ isActive }) =>
                    `text-sm font-medium ${isActive ? 'text-accent' : 'text-text'}`
                  }
                  onClick={() => setMenuOpen(false)}
                >
                  New case
                </NavLink>
              </li>
            </ul>
          </div>
        ) : null}
      </header>

      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6">
        <Outlet />
      </main>
    </div>
  )
}
