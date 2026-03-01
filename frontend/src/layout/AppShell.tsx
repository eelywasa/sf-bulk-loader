import { NavLink, Outlet } from 'react-router-dom'
import clsx from 'clsx'

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

export default function AppShell() {
  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      {/* Sidebar */}
      <aside className="w-56 bg-white border-r border-gray-200 flex flex-col flex-shrink-0">
        {/* Logo/brand */}
        <div className="px-5 py-4 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-blue-600 flex items-center justify-center">
              <span className="text-white text-xs font-bold">SF</span>
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

        {/* Footer */}
        <div className="px-5 py-3 border-t border-gray-200">
          <p className="text-xs text-gray-400">SF Bulk Loader v0.1</p>
        </div>
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
