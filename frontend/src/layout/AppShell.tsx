import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { type Theme, useTheme } from '../context/ThemeContext'
import { useAuth } from '../context/AuthContext'
import { usePermission } from '../hooks/usePermission'
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
  faEnvelope,
  faCloud,
  faTableColumns,
  faShieldHalved,
  faUser,
  faChevronDown,
  faChevronLeft,
  faChevronRight,
  faCheck,
  faRightFromBracket,
} from '@fortawesome/free-solid-svg-icons'

interface NavItem {
  path: string
  label: string
  icon: IconDefinition
  end?: boolean
  /** Permission key required to see this nav item. Omit for always-visible items. */
  permission?: string
}

const ALL_NAV_ITEMS: NavItem[] = [
  { path: '/', label: 'Dashboard', icon: faGaugeHigh, end: true },
  { path: '/files', label: 'Files', icon: faFolderOpen, permission: 'files.view' },
  { path: '/connections', label: 'Connections', icon: faPlug, permission: 'connections.view' },
  { path: '/plans', label: 'Load Plans', icon: faListCheck, permission: 'plans.view' },
  { path: '/runs', label: 'Runs', icon: faPlay, permission: 'runs.view' },
]

const themeOptions: { value: Theme; label: string }[] = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'system', label: 'System' },
]

function SettingsMenu({ collapsed }: { collapsed: boolean }) {
  const { theme, setTheme } = useTheme()
  const { authRequired } = useAuth()
  const canSettings = usePermission('system.settings')
  const navigate = useNavigate()
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
        className={clsx(
          'w-full flex items-center rounded text-sm text-content-secondary hover:bg-surface-hover hover:text-content-primary transition-colors',
          collapsed ? 'justify-center py-3' : 'gap-2 px-2 py-1.5'
        )}
        title={collapsed ? 'Settings' : undefined}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Settings"
      >
        <FontAwesomeIcon icon={faGear} className="w-4 h-4 flex-shrink-0" />
        {!collapsed && <span className="flex-1 text-left font-medium">Settings</span>}
        {!collapsed && (
          <FontAwesomeIcon
            icon={faChevronDown}
            className={clsx('w-3.5 h-3.5 transition-transform', open && 'rotate-180')}
          />
        )}
      </button>

      {/* Settings popover — floats above when expanded, flies right when collapsed */}
      {open && (
        <div className={clsx(
          'absolute bg-surface-elevated border border-border-base rounded-md shadow-lg z-50 overflow-visible min-w-[160px]',
          collapsed
            ? 'bottom-0 left-full ml-2'
            : 'bottom-full left-3 right-3 mb-1'
        )}>
          {/* Profile row — only visible on hosted profiles */}
          {authRequired && (
            <button
              onClick={() => { setOpen(false); setThemeOpen(false); navigate('/profile') }}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm text-content-secondary hover:bg-surface-hover transition-colors"
              role="menuitem"
            >
              <FontAwesomeIcon icon={faUser} className="w-3.5 h-3.5 flex-shrink-0" />
              <span>Profile</span>
            </button>
          )}

          {/* Admin settings rows — only shown to users with system.settings permission */}
          {authRequired && canSettings && (
            <>
              <button
                onClick={() => { setOpen(false); setThemeOpen(false); navigate('/settings/email') }}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-content-secondary hover:bg-surface-hover transition-colors"
                role="menuitem"
              >
                <FontAwesomeIcon icon={faEnvelope} className="w-3.5 h-3.5 flex-shrink-0" />
                <span>Email</span>
              </button>
              <button
                onClick={() => { setOpen(false); setThemeOpen(false); navigate('/settings/salesforce') }}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-content-secondary hover:bg-surface-hover transition-colors"
                role="menuitem"
              >
                <FontAwesomeIcon icon={faCloud} className="w-3.5 h-3.5 flex-shrink-0" />
                <span>Salesforce</span>
              </button>
              <button
                onClick={() => { setOpen(false); setThemeOpen(false); navigate('/settings/partitioning') }}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-content-secondary hover:bg-surface-hover transition-colors"
                role="menuitem"
              >
                <FontAwesomeIcon icon={faTableColumns} className="w-3.5 h-3.5 flex-shrink-0" />
                <span>Partitioning</span>
              </button>
              <button
                onClick={() => { setOpen(false); setThemeOpen(false); navigate('/settings/security') }}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-content-secondary hover:bg-surface-hover transition-colors"
                role="menuitem"
              >
                <FontAwesomeIcon icon={faShieldHalved} className="w-3.5 h-3.5 flex-shrink-0" />
                <span>Security</span>
              </button>
            </>
          )}

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
  const { user, logout, authRequired, permissions } = useAuth()
  const displayName = user?.display_name ?? user?.username ?? null
  const [collapsed, setCollapsed] = useState(() =>
    localStorage.getItem('sidebarCollapsed') === 'true'
  )

  // Filter nav items to only those the user has permission to see
  const navItems = ALL_NAV_ITEMS.filter(
    (item) => !item.permission || permissions.has(item.permission),
  )

  function toggleCollapsed() {
    const next = !collapsed
    setCollapsed(next)
    localStorage.setItem('sidebarCollapsed', String(next))
  }

  return (
    <div className="flex h-dvh overflow-hidden bg-surface-base">
      {/* Sidebar */}
      <aside className={clsx(
        'group/sidebar bg-surface-raised border-r border-border-base flex flex-col flex-shrink-0 transition-all duration-200',
        collapsed ? 'w-14' : 'w-56'
      )}>
        {/* Logo/brand */}
        {collapsed ? (
          <button
            onClick={toggleCollapsed}
            className="px-3 py-4 border-b border-border-base flex items-center justify-center text-content-muted hover:text-content-primary transition-colors"
            aria-label="Expand sidebar"
          >
            <div className="w-6 h-6 rounded bg-blue-600 flex items-center justify-center group-hover/sidebar:hidden">
              <FontAwesomeIcon icon={faHexagonNodes} className="w-4 h-4 text-white" />
            </div>
            <div className="w-6 h-6 hidden group-hover/sidebar:flex items-center justify-center">
              <FontAwesomeIcon icon={faChevronRight} className="w-4 h-4" />
            </div>
          </button>
        ) : (
          <div className="px-3 py-4 border-b border-border-base flex items-center justify-between min-w-0">
            <div className="flex items-center gap-2 min-w-0">
              <div className="w-6 h-6 rounded bg-blue-600 flex items-center justify-center flex-shrink-0">
                <FontAwesomeIcon icon={faHexagonNodes} className="w-4 h-4 text-white" />
              </div>
              <span className="text-sm font-semibold text-content-primary leading-tight truncate">
                Bulk Loader
              </span>
            </div>
            <button
              onClick={toggleCollapsed}
              className="w-5 h-5 flex items-center justify-center text-content-muted hover:text-content-primary transition-colors flex-shrink-0"
              aria-label="Collapse sidebar"
            >
              <FontAwesomeIcon icon={faChevronLeft} className="w-3 h-3" />
            </button>
          </div>
        )}

        {/* Navigation */}
        <nav className="flex-1 py-3 overflow-hidden" aria-label="Main navigation">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.end}
              title={collapsed ? item.label : undefined}
              className={({ isActive }) =>
                clsx(
                  'flex items-center text-sm font-medium transition-colors duration-100',
                  collapsed ? 'justify-center py-2.5' : 'gap-3 px-5 py-2.5',
                  isActive
                    ? 'bg-accent-soft text-content-selected border-r-2 border-accent'
                    : 'text-content-secondary hover:bg-surface-hover hover:text-content-primary',
                )
              }
            >
              <FontAwesomeIcon icon={item.icon} className="w-4 h-4 flex-shrink-0" />
              {!collapsed && item.label}
            </NavLink>
          ))}
        </nav>

        {/* Settings menu */}
        <SettingsMenu collapsed={collapsed} />
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
