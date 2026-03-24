import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import clsx from 'clsx'
import { type Theme, useTheme } from '../context/ThemeContext'
import { useAuth } from '../context/AuthContext'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import type { IconDefinition } from '@fortawesome/fontawesome-svg-core'
import {
  faHexagonNodes,
  faGaugeHigh,
  faPlug,
  faListCheck,
  faPlay,
  faFolderOpen,
  faGear,
  faChevronDown,
  faChevronRight,
  faCheck,
  faRightFromBracket,
} from '@fortawesome/free-solid-svg-icons'

interface NavItem {
  path: string
  label: string
  icon: IconDefinition
  end?: boolean
}

const navItems: NavItem[] = [
  { path: '/', label: 'Dashboard', icon: faGaugeHigh, end: true },
  { path: '/files', label: 'Input Files', icon: faFolderOpen },
  { path: '/connections', label: 'Connections', icon: faPlug },
  { path: '/plans', label: 'Load Plans', icon: faListCheck },
  { path: '/runs', label: 'Runs', icon: faPlay },
]

const themeOptions: { value: Theme; label: string }[] = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'system', label: 'System' },
]

function SettingsMenu() {
  const { theme, setTheme } = useTheme()
  const [open, setOpen] = useState(false)
  const [themeOpen, setThemeOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    if (!open) return
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false)
        setThemeOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  function handleThemeSelect(t: Theme) {
    setTheme(t)
    setOpen(false)
    setThemeOpen(false)
  }

  return (
    <div ref={menuRef} className="relative px-3 py-3 border-t border-border-base">
      <button
        onClick={() => { setOpen(v => !v); setThemeOpen(false) }}
        className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-sm text-content-secondary hover:bg-surface-hover hover:text-content-primary transition-colors"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <FontAwesomeIcon icon={faGear} className="w-4 h-4 flex-shrink-0" />
        <span className="flex-1 text-left font-medium">Settings</span>
        <FontAwesomeIcon
          icon={faChevronDown}
          className={clsx('w-3.5 h-3.5 transition-transform', open && 'rotate-180')}
        />
      </button>

      {/* Settings popover — floats above the button */}
      {open && (
        <div className="absolute bottom-full left-3 right-3 mb-1 bg-surface-elevated border border-border-base rounded-md shadow-lg z-50 overflow-visible">
          {/* Theme row */}
          <div className="relative">
            <button
              onClick={() => setThemeOpen(v => !v)}
              className="w-full flex items-center justify-between px-3 py-2 text-sm text-content-secondary hover:bg-surface-hover transition-colors"
              aria-haspopup="menu"
              aria-expanded={themeOpen}
            >
              <span>Theme</span>
              <FontAwesomeIcon
                icon={faChevronRight}
                className={clsx('w-3.5 h-3.5 transition-transform', themeOpen && 'rotate-90')}
              />
            </button>

            {/* Theme submenu — floats to the right of the popover */}
            {themeOpen && (
              <div className="absolute bottom-0 left-full ml-1 bg-surface-elevated border border-border-base rounded-md shadow-lg z-50 min-w-[120px]">
                {themeOptions.map(opt => (
                  <button
                    key={opt.value}
                    onClick={() => handleThemeSelect(opt.value)}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-content-secondary hover:bg-surface-hover transition-colors"
                    role="menuitemradio"
                    aria-checked={theme === opt.value}
                  >
                    {/* Checkmark placeholder — keeps alignment consistent */}
                    <span className="w-3.5 h-3.5 flex items-center justify-center flex-shrink-0">
                      {theme === opt.value && (
                        <FontAwesomeIcon icon={faCheck} className="w-3.5 h-3.5 text-accent" />
                      )}
                    </span>
                    {opt.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default function AppShell() {
  const { user, logout, authRequired } = useAuth()
  const displayName = user?.display_name ?? user?.username ?? null

  return (
    <div className="flex h-screen overflow-hidden bg-surface-base">
      {/* Sidebar */}
      <aside className="w-56 bg-surface-raised border-r border-border-base flex flex-col flex-shrink-0">
        {/* Logo/brand */}
        <div className="px-5 py-4 border-b border-border-base">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-blue-600 flex items-center justify-center">
              <FontAwesomeIcon icon={faHexagonNodes} className="w-4 h-4 text-white" />
            </div>
            <span className="text-sm font-semibold text-content-primary leading-tight">
              Bulk Loader
            </span>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-3" aria-label="Main navigation">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.end}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 px-5 py-2.5 text-sm font-medium transition-colors duration-100',
                  isActive
                    ? 'bg-accent-soft text-content-selected border-r-2 border-accent'
                    : 'text-content-secondary hover:bg-surface-hover hover:text-content-primary',
                )
              }
            >
              <FontAwesomeIcon icon={item.icon} className="w-4 h-4 flex-shrink-0" />
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* Settings menu (replaces version string) */}
        <SettingsMenu />
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="bg-surface-raised border-b border-border-base px-6 py-3 flex items-center justify-between flex-shrink-0">
          <div />
          <div className="flex items-center gap-4">
            {displayName && (
              <span className="text-xs text-content-muted">{displayName}</span>
            )}
            {authRequired && (
              <button
                onClick={logout}
                className="flex items-center gap-1.5 text-xs text-content-muted hover:text-content-primary transition-colors"
                aria-label="Sign out"
              >
                <FontAwesomeIcon icon={faRightFromBracket} className="w-3.5 h-3.5" />
                Sign out
              </button>
            )}
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-auto min-w-0">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
