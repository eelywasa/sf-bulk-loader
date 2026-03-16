import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../../components/ui/Toast'
import FilesPage, { formatFileSize } from '../../pages/FilesPage'
import type { InputDirectoryEntry, InputFilePreview } from '../../api/types'

// ─── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('../../api/endpoints', () => ({
  filesApi: {
    listInput: vi.fn(),
    previewInput: vi.fn(),
  },
}))

import { filesApi } from '../../api/endpoints'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const fileList: InputDirectoryEntry[] = [
  { name: 'accounts.csv', kind: 'file', path: 'accounts.csv', size_bytes: 2048, row_count: 100 },
  { name: 'contacts.csv', kind: 'file', path: 'contacts.csv', size_bytes: 1048576, row_count: 500 },
  { name: 'opportunities.csv', kind: 'file', path: 'opportunities.csv', size_bytes: 5242880, row_count: 2000 },
]

const mixedList: InputDirectoryEntry[] = [
  { name: '2026', kind: 'directory', path: '2026', size_bytes: null, row_count: null },
  { name: 'accounts.csv', kind: 'file', path: 'accounts.csv', size_bytes: 2048, row_count: 100 },
]

const accountsPreview: InputFilePreview = {
  filename: 'accounts.csv',
  header: ['Name', 'ExternalId__c', 'BillingCity'],
  rows: [
    { Name: 'Acme Corp', ExternalId__c: 'ACCT-001', BillingCity: 'New York' },
    { Name: 'Globex', ExternalId__c: 'ACCT-002', BillingCity: 'Springfield' },
  ],
  row_count: 150,
}

const widePreview: InputFilePreview = {
  filename: 'wide.csv',
  header: Array.from({ length: 20 }, (_, i) => `Column${i + 1}`),
  rows: [Object.fromEntries(Array.from({ length: 20 }, (_, i) => [`Column${i + 1}`, `val${i}`]))],
  row_count: 1,
}

// ─── Render helper ────────────────────────────────────────────────────────────

function renderFilesPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter>
          <FilesPage />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

// ─── Unit tests: formatFileSize ───────────────────────────────────────────────

describe('formatFileSize', () => {
  it('formats bytes below 1 KB as B', () => {
    expect(formatFileSize(512)).toBe('512 B')
  })

  it('formats 0 bytes', () => {
    expect(formatFileSize(0)).toBe('0 B')
  })

  it('formats exactly 1023 bytes as B', () => {
    expect(formatFileSize(1023)).toBe('1023 B')
  })

  it('formats 1 KB', () => {
    expect(formatFileSize(1024)).toBe('1.0 KB')
  })

  it('formats 2048 bytes as 2.0 KB', () => {
    expect(formatFileSize(2048)).toBe('2.0 KB')
  })

  it('formats 1 MB', () => {
    expect(formatFileSize(1048576)).toBe('1.0 MB')
  })

  it('formats 5 MB', () => {
    expect(formatFileSize(5242880)).toBe('5.0 MB')
  })
})

// ─── FilesPage component tests ────────────────────────────────────────────────

describe('FilesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ── Loading state ──────────────────────────────────────────────────────────

  it('shows loading indicator while file list is fetching', () => {
    vi.mocked(filesApi.listInput).mockReturnValue(new Promise(() => {}))
    renderFilesPage()
    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
  })

  // ── Error state ────────────────────────────────────────────────────────────

  it('shows error message when file list fetch fails', async () => {
    vi.mocked(filesApi.listInput).mockRejectedValue(new Error('Network error'))
    renderFilesPage()
    await waitFor(() => {
      expect(screen.getByText('Failed to load input files')).toBeInTheDocument()
    })
  })

  it('shows ApiError message when fetch fails with ApiError', async () => {
    const { ApiError } = await import('../../api/client')
    vi.mocked(filesApi.listInput).mockRejectedValue(
      new ApiError({ status: 500, message: 'Internal server error' }),
    )
    renderFilesPage()
    await waitFor(() => {
      expect(screen.getByText('Internal server error')).toBeInTheDocument()
    })
  })

  it('shows page heading even in error state', async () => {
    vi.mocked(filesApi.listInput).mockRejectedValue(new Error('fail'))
    renderFilesPage()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Input Files' })).toBeInTheDocument()
    })
  })

  // ── Empty state ────────────────────────────────────────────────────────────

  it('shows empty state when no files are returned', async () => {
    vi.mocked(filesApi.listInput).mockResolvedValue([])
    renderFilesPage()
    await waitFor(() => {
      expect(screen.getByText('No input files found')).toBeInTheDocument()
    })
  })

  it('shows empty state description when no files', async () => {
    vi.mocked(filesApi.listInput).mockResolvedValue([])
    renderFilesPage()
    await waitFor(() => {
      expect(
        screen.getByText(/Place CSV files in the \/data\/input directory/),
      ).toBeInTheDocument()
    })
  })

  it('shows page heading in empty state', async () => {
    vi.mocked(filesApi.listInput).mockResolvedValue([])
    renderFilesPage()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Input Files' })).toBeInTheDocument()
    })
  })

  // ── File list ──────────────────────────────────────────────────────────────

  it('renders the page heading when files are present', async () => {
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    renderFilesPage()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Input Files' })).toBeInTheDocument()
    })
  })

  it('renders all filenames in the list', async () => {
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    renderFilesPage()
    await waitFor(() => {
      expect(screen.getByText('accounts.csv')).toBeInTheDocument()
      expect(screen.getByText('contacts.csv')).toBeInTheDocument()
      expect(screen.getByText('opportunities.csv')).toBeInTheDocument()
    })
  })

  it('shows file sizes and row counts in the list', async () => {
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    renderFilesPage()
    await waitFor(() => {
      expect(screen.getByText('2.0 KB · 100 rows')).toBeInTheDocument()
      expect(screen.getByText('1.0 MB · 500 rows')).toBeInTheDocument()
      expect(screen.getByText('5.0 MB · 2,000 rows')).toBeInTheDocument()
    })
  })

  it('renders file list as a listbox', async () => {
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    renderFilesPage()
    await waitFor(() => {
      expect(screen.getByRole('listbox', { name: 'Input files' })).toBeInTheDocument()
    })
  })

  // ── No file selected state ─────────────────────────────────────────────────

  it('shows "No file selected" initially when files are loaded', async () => {
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    renderFilesPage()
    await waitFor(() => {
      expect(screen.getByText('No file selected')).toBeInTheDocument()
    })
  })

  it('does not call previewInput before any file is selected', async () => {
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    renderFilesPage()
    await waitFor(() => screen.getByText('No file selected'))
    expect(filesApi.previewInput).not.toHaveBeenCalled()
  })

  // ── File selection + preview loading ──────────────────────────────────────

  it('calls previewInput when a file is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockReturnValue(new Promise(() => {}))
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    expect(filesApi.previewInput).toHaveBeenCalledWith('accounts.csv', 25)
  })

  it('shows loading indicator while preview is fetching', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockReturnValue(new Promise(() => {}))
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    expect(screen.getByLabelText('Loading preview')).toBeInTheDocument()
  })

  it('removes "No file selected" once a file is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockReturnValue(new Promise(() => {}))
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    expect(screen.queryByText('No file selected')).not.toBeInTheDocument()
  })

  // ── Preview error ──────────────────────────────────────────────────────────

  it('shows preview error message when preview fetch fails', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockRejectedValue(new Error('Preview failed'))
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => {
      expect(screen.getByText('Failed to load file preview')).toBeInTheDocument()
    })
  })

  it('shows ApiError message for preview fetch failure', async () => {
    const user = userEvent.setup()
    const { ApiError } = await import('../../api/client')
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockRejectedValue(
      new ApiError({ status: 404, message: 'File not found on server' }),
    )
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => {
      expect(screen.getByText('File not found on server')).toBeInTheDocument()
    })
  })

  // ── Preview data ───────────────────────────────────────────────────────────

  it('shows the preview filename as a heading', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockResolvedValue(accountsPreview)
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'accounts.csv' })).toBeInTheDocument()
    })
  })

  it('shows the row count', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockResolvedValue(accountsPreview)
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => {
      expect(screen.getByText('150 rows')).toBeInTheDocument()
    })
  })

  it('renders column headers in the preview table', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockResolvedValue(accountsPreview)
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => {
      expect(screen.getByRole('columnheader', { name: 'Name' })).toBeInTheDocument()
      expect(screen.getByRole('columnheader', { name: 'ExternalId__c' })).toBeInTheDocument()
      expect(screen.getByRole('columnheader', { name: 'BillingCity' })).toBeInTheDocument()
    })
  })

  it('renders data rows in the preview table', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockResolvedValue(accountsPreview)
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => {
      expect(screen.getByText('Acme Corp')).toBeInTheDocument()
      expect(screen.getByText('ACCT-001')).toBeInTheDocument()
      expect(screen.getByText('New York')).toBeInTheDocument()
      expect(screen.getByText('Globex')).toBeInTheDocument()
    })
  })

  it('shows truncation note when preview rows < total row count', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockResolvedValue(accountsPreview) // 2 rows shown, 150 total
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => {
      expect(screen.getByText(/Showing first 2 of 150/)).toBeInTheDocument()
    })
  })

  it('does not show truncation note when all rows are displayed', async () => {
    const user = userEvent.setup()
    const fullPreview: InputFilePreview = { ...accountsPreview, row_count: 2 }
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockResolvedValue(fullPreview)
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => screen.getByText('2 rows'))
    expect(screen.queryByText(/Showing first/)).not.toBeInTheDocument()
  })

  // ── Horizontal scroll ──────────────────────────────────────────────────────

  it('wraps the preview table in an overflow-x-auto container', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue([
      { name: 'wide.csv', kind: 'file', path: 'wide.csv', size_bytes: 1024, row_count: 1 },
    ])
    vi.mocked(filesApi.previewInput).mockResolvedValue(widePreview)
    renderFilesPage()
    await waitFor(() => screen.getByText('wide.csv'))
    await user.click(screen.getByText('wide.csv'))
    await waitFor(() => screen.getByRole('table'))
    const table = screen.getByRole('table')
    expect(table.parentElement).toHaveClass('overflow-x-auto')
  })

  it('renders all 20 column headers for wide file', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue([
      { name: 'wide.csv', kind: 'file', path: 'wide.csv', size_bytes: 1024, row_count: 1 },
    ])
    vi.mocked(filesApi.previewInput).mockResolvedValue(widePreview)
    renderFilesPage()
    await waitFor(() => screen.getByText('wide.csv'))
    await user.click(screen.getByText('wide.csv'))
    await waitFor(() => {
      expect(screen.getByRole('columnheader', { name: 'Column1' })).toBeInTheDocument()
      expect(screen.getByRole('columnheader', { name: 'Column20' })).toBeInTheDocument()
    })
  })

  // ── File selection highlight ───────────────────────────────────────────────

  it('highlights the selected file in the list', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockReturnValue(new Promise(() => {}))
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    const btn = screen.getByRole('button', { name: /accounts\.csv/ })
    await user.click(btn)
    expect(btn).toHaveClass('bg-blue-50')
  })

  it('marks the selected file list item as aria-selected', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockReturnValue(new Promise(() => {}))
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => {
      const options = screen.getAllByRole('option')
      const accountsOption = options.find((o) => o.textContent?.includes('accounts.csv'))
      expect(accountsOption).toHaveAttribute('aria-selected', 'true')
    })
  })

  // ── Switching files ────────────────────────────────────────────────────────

  it('loads a new preview when switching to a different file', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)

    const contactsPreview: InputFilePreview = {
      filename: 'contacts.csv',
      header: ['FirstName', 'LastName', 'Email'],
      rows: [{ FirstName: 'Jane', LastName: 'Doe', Email: 'jane@example.com' }],
      row_count: 1,
    }

    vi.mocked(filesApi.previewInput)
      .mockResolvedValueOnce(accountsPreview)
      .mockResolvedValueOnce(contactsPreview)

    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))

    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => screen.getByRole('columnheader', { name: 'Name' }))

    await user.click(screen.getByText('contacts.csv'))
    await waitFor(() => {
      expect(screen.getByRole('columnheader', { name: 'FirstName' })).toBeInTheDocument()
      expect(screen.getByText('jane@example.com')).toBeInTheDocument()
    })
  })

  // ── Directory entries ──────────────────────────────────────────────────────

  it('shows directory entries in the file list', async () => {
    vi.mocked(filesApi.listInput).mockResolvedValue(mixedList)
    renderFilesPage()
    await waitFor(() => {
      expect(screen.getByText('2026')).toBeInTheDocument()
      expect(screen.getByText('accounts.csv')).toBeInTheDocument()
    })
  })

  it('does not show file size or row count for directory entries', async () => {
    vi.mocked(filesApi.listInput).mockResolvedValue(mixedList)
    renderFilesPage()
    await waitFor(() => screen.getByText('2026'))
    // File entry shows combined metadata; directory entry does not
    expect(screen.getByText('2.0 KB · 100 rows')).toBeInTheDocument()
    const dirButton = screen.getByRole('button', { name: /2026/ })
    expect(dirButton.textContent).not.toMatch(/KB|MB|rows/)
  })

  it('clicking a directory entry calls listInput with the directory path', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(mixedList)
    renderFilesPage()
    await waitFor(() => screen.getByText('2026'))
    await user.click(screen.getByText('2026'))
    expect(filesApi.listInput).toHaveBeenCalledWith('2026')
  })

  it('clicking a directory entry clears the selected file', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(mixedList)
    vi.mocked(filesApi.previewInput).mockReturnValue(new Promise(() => {}))
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    expect(screen.queryByText('No file selected')).not.toBeInTheDocument()
    await user.click(screen.getByText('2026'))
    await waitFor(() => {
      expect(screen.getByText('No file selected')).toBeInTheDocument()
    })
  })

  // ── Breadcrumb ─────────────────────────────────────────────────────────────

  it('renders the breadcrumb root "Input Files" link', async () => {
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    expect(screen.getByRole('button', { name: 'Input Files' })).toBeInTheDocument()
  })

  it('clicking breadcrumb root navigates back to root', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(mixedList)
    renderFilesPage()
    await waitFor(() => screen.getByText('2026'))
    await user.click(screen.getByText('2026'))
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    await user.click(screen.getByRole('button', { name: 'Input Files' }))
    expect(filesApi.listInput).toHaveBeenCalledWith('')
  })

  it('shows current directory segment in breadcrumb after navigation', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(mixedList)
    renderFilesPage()
    await waitFor(() => screen.getByText('2026'))
    await user.click(screen.getByText('2026'))
    await waitFor(() => {
      expect(screen.getByRole('navigation', { name: 'Directory breadcrumb' })).toHaveTextContent(
        '2026',
      )
    })
  })

  // ── Subdirectory file preview ──────────────────────────────────────────────

  it('calls previewInput with the full relative path for a file in a subdirectory', async () => {
    const user = userEvent.setup()
    const subEntries: InputDirectoryEntry[] = [
      { name: 'accounts.csv', kind: 'file', path: '2026/accounts.csv', size_bytes: 1024, row_count: 50 },
    ]
    vi.mocked(filesApi.listInput)
      .mockResolvedValueOnce(mixedList)
      .mockResolvedValueOnce(subEntries)
    vi.mocked(filesApi.previewInput).mockReturnValue(new Promise(() => {}))
    renderFilesPage()
    await waitFor(() => screen.getByText('2026'))
    await user.click(screen.getByText('2026'))
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    expect(filesApi.previewInput).toHaveBeenCalledWith('2026/accounts.csv', 25)
  })
})
