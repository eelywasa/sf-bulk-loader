/**
 * Tests for the /403 ForbiddenPage.
 */

import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ForbiddenPage from '../ForbiddenPage'

function renderForbiddenPage(locationState?: { requiredPermission?: string }) {
  return render(
    <MemoryRouter
      initialEntries={[{ pathname: '/403', state: locationState }]}
    >
      <Routes>
        <Route path="/403" element={<ForbiddenPage />} />
        <Route path="/" element={<div>Dashboard</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ForbiddenPage', () => {
  it('renders the 403 heading and back link', () => {
    renderForbiddenPage()

    expect(screen.getByText('403')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Access denied' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to dashboard' })).toBeInTheDocument()
  })

  it('does not show required permission when no state is provided', () => {
    renderForbiddenPage()

    expect(screen.queryByText(/required permission/i)).not.toBeInTheDocument()
  })

  it('shows the required permission key when passed via location state', () => {
    renderForbiddenPage({ requiredPermission: 'connections.manage' })

    expect(screen.getByText('connections.manage')).toBeInTheDocument()
    expect(screen.getByText(/required permission/i)).toBeInTheDocument()
  })

  it('back to dashboard link points to /', () => {
    renderForbiddenPage()

    const link = screen.getByRole('link', { name: 'Back to dashboard' })
    expect(link).toHaveAttribute('href', '/')
  })
})
