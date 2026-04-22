/**
 * Tests for SettingsPartitioningPage (SFBL-157).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '../context/ThemeContext'
import { AuthProvider } from '../context/AuthContext'
import { ToastProvider } from '../components/ui/Toast'
import * as client from '../api/client'
import * as endpoints from '../api/endpoints'
import SettingsPartitioningPage from './SettingsPartitioningPage'
import type { RuntimeConfig, UserResponse, CategorySettings } from '../api/types'

const MOCK_RUNTIME: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

const MOCK_USER: UserResponse = {
  id: 'u1',
  email: 'admin@example.com',
  display_name: 'Admin',
  is_active: true,
}

const MOCK_PARTITIONING_CATEGORY: CategorySettings = {
  category: 'partitioning',
  settings: [
    {
      key: 'default_partition_size',
      value: 10000,
      type: 'int',
      is_secret: false,
      description: 'Default number of records per CSV partition.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'max_parallel_jobs',
      value: 4,
      type: 'int',
      is_secret: false,
      description: 'Default maximum number of concurrent Bulk API jobs per run.',
      restart_required: false,
      updated_at: null,
    },
  ],
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

function renderPage() {
  const qc = makeQueryClient()
  return render(
    <ThemeProvider>
      <AuthProvider>
        <QueryClientProvider client={qc}>
          <ToastProvider>
            <MemoryRouter initialEntries={['/settings/partitioning']}>
              <SettingsPartitioningPage />
            </MemoryRouter>
          </ToastProvider>
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>,
  )
}

describe('SettingsPartitioningPage', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('auth_token', 'test-token')
    vi.spyOn(client, 'apiFetch')
    vi.spyOn(endpoints, 'getSettingsCategory')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders partitioning settings fields', async () => {
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.getSettingsCategory).mockResolvedValue({
      data: MOCK_PARTITIONING_CATEGORY,
      cacheTtl: 60,
    })

    renderPage()

    await waitFor(() => {
      expect(screen.getByLabelText('default_partition_size')).toBeInTheDocument()
    })
    expect(screen.getByLabelText('max_parallel_jobs')).toBeInTheDocument()
    expect(screen.getByText('Partitioning Settings')).toBeInTheDocument()
  })
})
