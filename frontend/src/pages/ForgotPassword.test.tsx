import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ThemeProvider } from '../context/ThemeContext'
import { ApiError } from '../api/client'
import * as endpoints from '../api/endpoints'
import ForgotPassword from './ForgotPassword'

function renderForgotPassword() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={['/forgot-password']}>
        <Routes>
          <Route path="/forgot-password" element={<ForgotPassword />} />
          <Route path="/login" element={<div>Login page</div>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  )
}

describe('ForgotPassword page', () => {
  beforeEach(() => {
    vi.spyOn(endpoints, 'requestPasswordReset')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the email input and submit button', () => {
    renderForgotPassword()
    expect(screen.getByLabelText('Email address')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /send reset link/i })).toBeInTheDocument()
  })

  it('transitions to confirmation state on 202 (success)', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.requestPasswordReset).mockResolvedValueOnce(undefined)

    renderForgotPassword()

    await user.type(screen.getByLabelText('Email address'), 'alice@example.com')
    await user.click(screen.getByRole('button', { name: /send reset link/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        'If an account exists for alice@example.com',
      )
    })

    // Form is gone, confirmation shown
    expect(screen.queryByLabelText('Email address')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /back to login/i })).toBeInTheDocument()
  })

  it('shows warning on 429 and stays on form', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.requestPasswordReset).mockRejectedValueOnce(
      new ApiError({ status: 429, message: 'Too Many Requests' }),
    )

    renderForgotPassword()

    await user.type(screen.getByLabelText('Email address'), 'alice@example.com')
    await user.click(screen.getByRole('button', { name: /send reset link/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Too many requests')
    })

    // Still on form
    expect(screen.getByLabelText('Email address')).toBeInTheDocument()
  })

  it('shows error on unexpected failure and stays on form', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.requestPasswordReset).mockRejectedValueOnce(
      new ApiError({ status: 500, message: 'Internal Server Error' }),
    )

    renderForgotPassword()

    await user.type(screen.getByLabelText('Email address'), 'alice@example.com')
    await user.click(screen.getByRole('button', { name: /send reset link/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong')
    })

    // Still on form
    expect(screen.getByLabelText('Email address')).toBeInTheDocument()
  })

  it('returns to form state when "Try a different email" is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.requestPasswordReset).mockResolvedValueOnce(undefined)

    renderForgotPassword()

    await user.type(screen.getByLabelText('Email address'), 'alice@example.com')
    await user.click(screen.getByRole('button', { name: /send reset link/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('If an account exists')
    })

    await user.click(screen.getByRole('button', { name: /try a different email/i }))

    expect(screen.getByLabelText('Email address')).toBeInTheDocument()
  })
})
