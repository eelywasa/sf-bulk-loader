import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import AppShell from '../../layout/AppShell'

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
  return render(<RouterProvider router={router} />)
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
})
