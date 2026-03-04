import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider, useTheme } from '../../context/ThemeContext'

// Helper component that exposes theme state for assertions
function ThemeDisplay() {
  const { theme, setTheme } = useTheme()
  return (
    <div>
      <span data-testid="theme">{theme}</span>
      <button onClick={() => setTheme('light')}>Set Light</button>
      <button onClick={() => setTheme('dark')}>Set Dark</button>
      <button onClick={() => setTheme('system')}>Set System</button>
    </div>
  )
}

function renderTheme() {
  return render(
    <ThemeProvider>
      <ThemeDisplay />
    </ThemeProvider>,
  )
}

describe('ThemeContext', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.classList.remove('dark')
  })

  afterEach(() => {
    localStorage.clear()
    document.documentElement.classList.remove('dark')
    vi.restoreAllMocks()
  })

  describe('initial theme', () => {
    it('defaults to "system" when localStorage is empty', () => {
      renderTheme()
      expect(screen.getByTestId('theme').textContent).toBe('system')
    })

    it('reads "light" from localStorage', () => {
      localStorage.setItem('theme', 'light')
      renderTheme()
      expect(screen.getByTestId('theme').textContent).toBe('light')
    })

    it('reads "dark" from localStorage', () => {
      localStorage.setItem('theme', 'dark')
      renderTheme()
      expect(screen.getByTestId('theme').textContent).toBe('dark')
    })

    it('reads "system" from localStorage', () => {
      localStorage.setItem('theme', 'system')
      renderTheme()
      expect(screen.getByTestId('theme').textContent).toBe('system')
    })

    it('falls back to "system" for unknown stored values', () => {
      localStorage.setItem('theme', 'invalid')
      renderTheme()
      expect(screen.getByTestId('theme').textContent).toBe('system')
    })
  })

  describe('dark class on <html>', () => {
    it('adds "dark" class when theme is set to dark', async () => {
      const user = userEvent.setup()
      renderTheme()
      await user.click(screen.getByRole('button', { name: 'Set Dark' }))
      expect(document.documentElement.classList.contains('dark')).toBe(true)
    })

    it('removes "dark" class when theme is set to light', async () => {
      const user = userEvent.setup()
      document.documentElement.classList.add('dark')
      renderTheme()
      await user.click(screen.getByRole('button', { name: 'Set Light' }))
      expect(document.documentElement.classList.contains('dark')).toBe(false)
    })

    it('starts with "dark" class when localStorage has "dark"', () => {
      localStorage.setItem('theme', 'dark')
      renderTheme()
      expect(document.documentElement.classList.contains('dark')).toBe(true)
    })

    it('removes "dark" class when localStorage has "light"', () => {
      document.documentElement.classList.add('dark')
      localStorage.setItem('theme', 'light')
      renderTheme()
      expect(document.documentElement.classList.contains('dark')).toBe(false)
    })
  })

  describe('localStorage persistence', () => {
    it('writes to localStorage when theme changes', async () => {
      const user = userEvent.setup()
      renderTheme()
      await user.click(screen.getByRole('button', { name: 'Set Dark' }))
      expect(localStorage.getItem('theme')).toBe('dark')
    })

    it('updates localStorage when switching from dark to light', async () => {
      const user = userEvent.setup()
      localStorage.setItem('theme', 'dark')
      renderTheme()
      await user.click(screen.getByRole('button', { name: 'Set Light' }))
      expect(localStorage.getItem('theme')).toBe('light')
    })

    it('updates localStorage when switching to system', async () => {
      const user = userEvent.setup()
      renderTheme()
      await user.click(screen.getByRole('button', { name: 'Set System' }))
      expect(localStorage.getItem('theme')).toBe('system')
    })
  })

  describe('system theme / OS preference', () => {
    it('applies dark class when system theme and OS prefers dark', () => {
      vi.spyOn(window, 'matchMedia').mockReturnValue({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      } as unknown as MediaQueryList)

      localStorage.setItem('theme', 'system')
      renderTheme()
      expect(document.documentElement.classList.contains('dark')).toBe(true)
    })

    it('does not apply dark class when system theme and OS prefers light', () => {
      vi.spyOn(window, 'matchMedia').mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      } as unknown as MediaQueryList)

      localStorage.setItem('theme', 'system')
      renderTheme()
      expect(document.documentElement.classList.contains('dark')).toBe(false)
    })

    it('attaches matchMedia listener when theme is system', () => {
      const addEventListener = vi.fn()
      vi.spyOn(window, 'matchMedia').mockReturnValue({
        matches: false,
        addEventListener,
        removeEventListener: vi.fn(),
      } as unknown as MediaQueryList)

      localStorage.setItem('theme', 'system')
      renderTheme()
      expect(addEventListener).toHaveBeenCalledWith('change', expect.any(Function))
    })

    it('updates dark class when OS preference changes while on system theme', async () => {
      let changeHandler: ((e: { matches: boolean }) => void) | undefined
      vi.spyOn(window, 'matchMedia').mockReturnValue({
        matches: false,
        addEventListener: (_: string, fn: (e: { matches: boolean }) => void) => {
          changeHandler = fn
        },
        removeEventListener: vi.fn(),
      } as unknown as MediaQueryList)

      localStorage.setItem('theme', 'system')
      renderTheme()
      expect(document.documentElement.classList.contains('dark')).toBe(false)

      // Simulate OS switching to dark
      act(() => changeHandler?.({ matches: true }))
      expect(document.documentElement.classList.contains('dark')).toBe(true)
    })
  })

  describe('useTheme outside provider', () => {
    it('throws when used outside ThemeProvider', () => {
      // Suppress React's error boundary console output
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      expect(() => render(<ThemeDisplay />)).toThrow('useTheme must be used within ThemeProvider')
      consoleSpy.mockRestore()
    })
  })
})
