import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import AppShell from '../../layout/AppShell'
import { ThemeProvider } from '../../context/ThemeContext'

function renderAppShell(initialPath = '/') {
  const router = createMemoryRouter(
    [
      {
        element: <AppShell />,
        children: [
          { path: '/', element: <div>Dashboard page</div> },
          { path: '/connections', element: <div>Connections page</div> },
          { path: '/plans', element: <div>Plans page</div> },
          { path: '/runs', element: <div>Runs page</div> },
          { path: '/files', element: <div>Files page</div> },
        ],
      },
    ],
    { initialEntries: [initialPath] },
  )
  return render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  )
}

describe('AppShell', () => {
  it('renders the brand name', () => {
    renderAppShell()
    expect(screen.getByText('Bulk Loader')).toBeInTheDocument()
  })

  it('renders all navigation links', () => {
    renderAppShell()
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Connections' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Load Plans' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Runs' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Files' })).toBeInTheDocument()
  })

  it('renders the outlet content for the current route', () => {
    renderAppShell('/')
    expect(screen.getByText('Dashboard page')).toBeInTheDocument()
  })

  it('renders connections page at /connections', () => {
    renderAppShell('/connections')
    expect(screen.getByText('Connections page')).toBeInTheDocument()
  })

  it('dashboard link has correct href', () => {
    renderAppShell()
    expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('href', '/')
  })

  it('connections link has correct href', () => {
    renderAppShell()
    expect(screen.getByRole('link', { name: 'Connections' })).toHaveAttribute(
      'href',
      '/connections',
    )
  })

  it('plans link has correct href', () => {
    renderAppShell()
    expect(screen.getByRole('link', { name: 'Load Plans' })).toHaveAttribute('href', '/plans')
  })

  it('has a main navigation landmark', () => {
    renderAppShell()
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument()
  })

  it('renders Settings button instead of version string', () => {
    renderAppShell()
    expect(screen.getByRole('button', { name: /settings/i })).toBeInTheDocument()
    expect(screen.queryByText('SF Bulk Loader v0.1')).not.toBeInTheDocument()
  })

  it('Settings menu is closed by default', () => {
    renderAppShell()
    expect(screen.queryByRole('button', { name: /theme/i })).not.toBeInTheDocument()
  })

  it('opens Settings menu on click', async () => {
    const user = userEvent.setup()
    renderAppShell()
    await user.click(screen.getByRole('button', { name: /settings/i }))
    expect(screen.getByRole('button', { name: /theme/i })).toBeInTheDocument()
  })

  it('opens Theme submenu on click', async () => {
    const user = userEvent.setup()
    renderAppShell()
    await user.click(screen.getByRole('button', { name: /settings/i }))
    await user.click(screen.getByRole('button', { name: /theme/i }))
    expect(screen.getByRole('menuitemradio', { name: /light/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitemradio', { name: /dark/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitemradio', { name: /system/i })).toBeInTheDocument()
  })

  it('closes menu after selecting a theme', async () => {
    const user = userEvent.setup()
    renderAppShell()
    await user.click(screen.getByRole('button', { name: /settings/i }))
    await user.click(screen.getByRole('button', { name: /theme/i }))
    await user.click(screen.getByRole('menuitemradio', { name: /light/i }))
    expect(screen.queryByRole('button', { name: /theme/i })).not.toBeInTheDocument()
  })

  it('renders an icon for each nav item', () => {
    renderAppShell()
    const nav = screen.getByRole('navigation', { name: 'Main navigation' })
    const links = within(nav).getAllByRole('link')
    links.forEach((link) => {
      expect(link.querySelector('svg')).toBeInTheDocument()
    })
  })

  it('renders the logo icon in the brand area', () => {
    const { container } = renderAppShell()
    const brand = container.querySelector('.px-5.py-4')
    expect(brand?.querySelector('svg')).toBeInTheDocument()
  })
})
