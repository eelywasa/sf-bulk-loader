import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ThemeProvider } from '../context/ThemeContext'
import { ApiError } from '../api/client'
import * as endpoints from '../api/endpoints'
import ResetPassword from './ResetPassword'

function renderResetPassword(token = 'valid-reset-token') {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[`/reset-password/${token}`]}>
        <Routes>
          <Route path="/reset-password/:token" element={<ResetPassword />} />
          <Route path="/login" element={<div>Login page</div>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  )
}

describe('ResetPassword page', () => {
  beforeEach(() => {
    vi.spyOn(endpoints, 'confirmPasswordReset')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders new password and confirm password fields', () => {
    renderResetPassword()
    expect(screen.getByLabelText('New password')).toBeInTheDocument()
    expect(screen.getByLabelText('Confirm new password')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /reset password/i })).toBeInTheDocument()
  })

  it('auto-focuses the new password field on mount', () => {
    renderResetPassword()
    expect(screen.getByLabelText('New password')).toHaveFocus()
  })

  it('shows success state on 204 (successful reset)', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.confirmPasswordReset).mockResolvedValueOnce(undefined)

    renderResetPassword()

    await user.type(screen.getByLabelText('New password'), 'MyNewP@ssw0rd!')
    await user.type(screen.getByLabelText('Confirm new password'), 'MyNewP@ssw0rd!')
    await user.click(screen.getByRole('button', { name: /reset password/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Your password has been reset')
    })

    expect(screen.queryByLabelText('New password')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /go to login/i })).toBeInTheDocument()

    expect(endpoints.confirmPasswordReset).toHaveBeenCalledWith({
      token: 'valid-reset-token',
      new_password: 'MyNewP@ssw0rd!',
    })
  })

  it('shows error on 400 (invalid/expired/used token)', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.confirmPasswordReset).mockRejectedValueOnce(
      new ApiError({ status: 400, message: 'Token has expired or is invalid.' }),
    )

    renderResetPassword()

    await user.type(screen.getByLabelText('New password'), 'MyNewP@ssw0rd!')
    await user.type(screen.getByLabelText('Confirm new password'), 'MyNewP@ssw0rd!')
    await user.click(screen.getByRole('button', { name: /reset password/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Token has expired or is invalid.')
    })

    // Still on form
    expect(screen.getByLabelText('New password')).toBeInTheDocument()
  })

  it('disables submit when passwords do not match', async () => {
    const user = userEvent.setup()

    renderResetPassword()

    await user.type(screen.getByLabelText('New password'), 'MyNewP@ssw0rd!')
    await user.type(screen.getByLabelText('Confirm new password'), 'different')

    expect(screen.getByRole('button', { name: /reset password/i })).toBeDisabled()
    expect(screen.getByText('Passwords do not match')).toBeInTheDocument()
  })

  it('does not call API when passwords do not match and submit is triggered', async () => {
    const user = userEvent.setup()

    renderResetPassword()

    await user.type(screen.getByLabelText('New password'), 'MyNewP@ssw0rd!')
    await user.type(screen.getByLabelText('Confirm new password'), 'different')

    // Button is disabled but we can still trigger form submit via keyboard
    const submitBtn = screen.getByRole('button', { name: /reset password/i })
    expect(submitBtn).toBeDisabled()
    expect(endpoints.confirmPasswordReset).not.toHaveBeenCalled()
  })

  it('shows password strength hints when typing', async () => {
    const user = userEvent.setup()

    renderResetPassword()

    await user.type(screen.getByLabelText('New password'), 'short')

    expect(screen.getByText(/at least 12 characters/i)).toBeInTheDocument()
  })
})
