import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import clsx from 'clsx'
import { type Theme, useTheme } from '../context/ThemeContext'

interface NavItem {
  path: string
  label: string
  end?: boolean
}

const navItems: NavItem[] = [
  { path: '/', label: 'Dashboard', end: true },
  { path: '/connections', label: 'Connections' },
  { path: '/plans', label: 'Load Plans' },
  { path: '/runs', label: 'Runs' },
  { path: '/files', label: 'Files' },
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
    <div ref={menuRef} className="relative px-3 py-3 border-t border-gray-200">
      <button
        onClick={() => { setOpen(v => !v); setThemeOpen(false) }}
        className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-sm text-gray-600 hover:bg-gray-50 hover:text-gray-900 transition-colors"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {/* Gear icon */}
        <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth={1.75} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
        <span className="flex-1 text-left font-medium">Settings</span>
        {/* Chevron up when open, down when closed */}
        <svg className={clsx('w-3.5 h-3.5 transition-transform', open && 'rotate-180')} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Settings popover — floats above the button */}
      {open && (
        <div className="absolute bottom-full left-3 right-3 mb-1 bg-white border border-gray-200 rounded-md shadow-lg z-50 overflow-visible">
          {/* Theme row */}
          <div className="relative">
            <button
              onClick={() => setThemeOpen(v => !v)}
              className="w-full flex items-center justify-between px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
              aria-haspopup="menu"
              aria-expanded={themeOpen}
            >
              <span>Theme</span>
              <svg className={clsx('w-3.5 h-3.5 transition-transform', themeOpen && 'rotate-90')} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
              </svg>
            </button>

            {/* Theme submenu — floats to the right of the popover */}
            {themeOpen && (
              <div className="absolute bottom-0 left-full ml-1 bg-white border border-gray-200 rounded-md shadow-lg z-50 min-w-[120px]">
                {themeOptions.map(opt => (
                  <button
                    key={opt.value}
                    onClick={() => handleThemeSelect(opt.value)}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
                    role="menuitemradio"
                    aria-checked={theme === opt.value}
                  >
                    {/* Checkmark placeholder — keeps alignment consistent */}
                    <span className="w-3.5 h-3.5 flex items-center justify-center flex-shrink-0">
                      {theme === opt.value && (
                        <svg className="w-3.5 h-3.5 text-blue-600" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
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
  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      {/* Sidebar */}
      <aside className="w-56 bg-white border-r border-gray-200 flex flex-col flex-shrink-0">
        {/* Logo/brand */}
        <div className="px-5 py-4 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-blue-600 flex items-center justify-center">
              <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className="w-4 h-4 text-white">
                <path d="M6 6h6a2 2 0 0 1 2 2v2" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                <path d="M18 18h-6a2 2 0 0 1-2-2v-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                <path d="M10 12h8" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                <path d="M16 10l2 2-2 2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M4 6h2M18 18h2" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              </svg>
            </div>
            <span className="text-sm font-semibold text-gray-900 leading-tight">
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
                  'flex items-center px-5 py-2.5 text-sm font-medium transition-colors duration-100',
                  isActive
                    ? 'bg-blue-50 text-blue-700 border-r-2 border-blue-600'
                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900',
                )
              }
            >
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
        <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between flex-shrink-0">
          <div />
          <div className="text-xs text-gray-400">Salesforce Bulk Loader</div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
