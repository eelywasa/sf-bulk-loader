import { describe, it, expect, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToastProvider, useToast } from '../../components/ui/Toast'

function ToastTrigger() {
  const toast = useToast()
  return (
    <div>
      <button onClick={() => toast.success('Saved!')}>Success</button>
      <button onClick={() => toast.error('Failed!')}>Error</button>
      <button onClick={() => toast.info('Info message')}>Info</button>
      <button onClick={() => toast.warning('Watch out')}>Warning</button>
    </div>
  )
}

function renderToasts() {
  return render(
    <ToastProvider>
      <ToastTrigger />
    </ToastProvider>,
  )
}

describe('Toast / useToast', () => {
  it('throws when useToast is called outside provider', () => {
    // Suppress console.error for this test
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<ToastTrigger />)).toThrow()
    spy.mockRestore()
  })

  it('shows a success toast when success() is called', async () => {
    const user = userEvent.setup()
    renderToasts()
    await user.click(screen.getByRole('button', { name: 'Success' }))
    expect(screen.getByText('Saved!')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('shows an error toast when error() is called', async () => {
    const user = userEvent.setup()
    renderToasts()
    await user.click(screen.getByRole('button', { name: 'Error' }))
    expect(screen.getByText('Failed!')).toBeInTheDocument()
  })

  it('shows info and warning toasts', async () => {
    const user = userEvent.setup()
    renderToasts()
    await user.click(screen.getByRole('button', { name: 'Info' }))
    await user.click(screen.getByRole('button', { name: 'Warning' }))
    expect(screen.getByText('Info message')).toBeInTheDocument()
    expect(screen.getByText('Watch out')).toBeInTheDocument()
  })

  it('dismisses toast when close button is clicked', async () => {
    const user = userEvent.setup()
    renderToasts()
    await user.click(screen.getByRole('button', { name: 'Success' }))
    expect(screen.getByText('Saved!')).toBeInTheDocument()
    const dismiss = screen.getByRole('button', { name: /dismiss/i })
    await user.click(dismiss)
    expect(screen.queryByText('Saved!')).not.toBeInTheDocument()
  })

  it('auto-dismisses toast after 5 seconds', () => {
    vi.useFakeTimers()
    renderToasts()
    // Use synchronous fireEvent so fake timers don't interfere with user-event internals
    act(() => {
      screen.getByRole('button', { name: 'Success' }).click()
    })
    expect(screen.getByText('Saved!')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(5001)
    })
    expect(screen.queryByText('Saved!')).not.toBeInTheDocument()
    vi.useRealTimers()
  })

  it('renders an svg icon in each toast type', async () => {
    const user = userEvent.setup()
    renderToasts()
    for (const btnName of ['Success', 'Error', 'Info', 'Warning']) {
      await user.click(screen.getByRole('button', { name: btnName }))
    }
    const alerts = screen.getAllByRole('alert')
    expect(alerts).toHaveLength(4)
    alerts.forEach((alert) => {
      expect(alert.querySelector('svg')).toBeInTheDocument()
    })
  })
})
