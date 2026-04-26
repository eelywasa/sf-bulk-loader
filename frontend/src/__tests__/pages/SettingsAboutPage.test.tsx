import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import * as endpoints from '../../api/endpoints'
import type { AboutPayload } from '../../api/types'
import SettingsAboutPage from '../../pages/SettingsAboutPage'

// ─── Mock payload ─────────────────────────────────────────────────────────────

const MOCK_ABOUT: AboutPayload = {
  app: {
    version: '0.8.42',
    git_sha: 'abc1234',
    build_time: '2026-04-26T10:00:00Z',
  },
  distribution: {
    profile: 'self_hosted',
    auth_mode: 'local',
  },
  runtime: {
    python_version: '3.12.7',
    fastapi_version: '0.115.0',
  },
  database: {
    backend: 'sqlite',
    alembic_head: '0029',
  },
  salesforce: {
    api_version: 'v62.0',
  },
  email: {
    backend: 'smtp',
    enabled: true,
  },
  storage: {
    input_connections: { local: 1 },
    output_connections: { s3: 1 },
  },
}

const MOCK_ABOUT_UNKNOWN: AboutPayload = {
  app: { version: '0.0.0-dev', git_sha: 'unknown', build_time: 'unknown' },
  distribution: { profile: 'self_hosted', auth_mode: 'local' },
  runtime: { python_version: '3.12.7', fastapi_version: 'unknown' },
  database: { backend: 'sqlite', alembic_head: 'unknown' },
  salesforce: { api_version: 'v62.0' },
  email: { backend: 'noop', enabled: false },
  storage: { input_connections: {}, output_connections: {} },
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function renderPage(queryClient = makeClient()) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SettingsAboutPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('SettingsAboutPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('renders all expected sections with mock payload', async () => {
    vi.spyOn(endpoints, 'getAbout').mockResolvedValue(MOCK_ABOUT)

    renderPage()

    await waitFor(() => expect(screen.getByText('App')).toBeInTheDocument())

    // Section headings
    expect(screen.getByText('Distribution')).toBeInTheDocument()
    expect(screen.getByText('Runtime')).toBeInTheDocument()
    expect(screen.getByText('Database')).toBeInTheDocument()
    expect(screen.getByText('Salesforce')).toBeInTheDocument()
    // "Email & Storage" section heading
    expect(screen.getByRole('heading', { name: /Email/i })).toBeInTheDocument()

    // Spot-check values
    expect(screen.getByText('0.8.42')).toBeInTheDocument()
    expect(screen.getByText('abc1234')).toBeInTheDocument()
    expect(screen.getByText('self_hosted')).toBeInTheDocument()
    expect(screen.getByText('3.12.7')).toBeInTheDocument()
    expect(screen.getByText('sqlite')).toBeInTheDocument()
    expect(screen.getByText('v62.0')).toBeInTheDocument()
  })

  it('renders — for unknown / empty values', async () => {
    vi.spyOn(endpoints, 'getAbout').mockResolvedValue(MOCK_ABOUT_UNKNOWN)

    renderPage()

    await waitFor(() => expect(screen.getByText('App')).toBeInTheDocument())

    // 'unknown' values should render as '—', not as 'unknown'
    // There should be multiple '—' cells
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThan(0)

    // The word 'unknown' must not appear as raw visible text
    expect(screen.queryByText('unknown')).toBeNull()
  })

  it('copies JSON to clipboard when Copy as JSON is clicked', async () => {
    vi.spyOn(endpoints, 'getAbout').mockResolvedValue(MOCK_ABOUT)

    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    renderPage()

    await waitFor(() => expect(screen.getByText('Copy as JSON')).toBeInTheDocument())

    await userEvent.click(screen.getByText('Copy as JSON'))

    expect(writeText).toHaveBeenCalledOnce()
    const arg = writeText.mock.calls[0][0]
    const parsed = JSON.parse(arg)
    expect(parsed.app.version).toBe('0.8.42')
  })

  it('shows loading state initially', () => {
    vi.spyOn(endpoints, 'getAbout').mockReturnValue(new Promise(() => {}))

    renderPage()

    expect(screen.getByText('Loading…')).toBeInTheDocument()
  })

  it('shows error message when fetch fails', async () => {
    vi.spyOn(endpoints, 'getAbout').mockRejectedValue(new Error('Network error'))

    renderPage()

    await waitFor(() =>
      expect(screen.getByText(/Failed to load system info/)).toBeInTheDocument(),
    )
  })
})
