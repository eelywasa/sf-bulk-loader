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
  inputConnectionsApi: {
    list: vi.fn(),
  },
}))

import { filesApi, inputConnectionsApi } from '../../api/endpoints'
import type { InputConnection } from '../../api/types'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const s3Connection: InputConnection = {
  id: 'conn-s3-1',
  name: 'Production S3',
  provider: 's3',
  bucket: 'my-data-bucket',
  root_prefix: 'data/csvs/',
  region: 'us-east-1',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const fileList: InputDirectoryEntry[] = [
  { name: 'accounts.csv', kind: 'file', path: 'accounts.csv', size_bytes: 2048, row_count: 100, source: 'local', provider: 'local' },
  { name: 'contacts.csv', kind: 'file', path: 'contacts.csv', size_bytes: 1048576, row_count: 500, source: 'local', provider: 'local' },
  { name: 'opportunities.csv', kind: 'file', path: 'opportunities.csv', size_bytes: 5242880, row_count: 2000, source: 'local', provider: 'local' },
]

const mixedList: InputDirectoryEntry[] = [
  { name: '2026', kind: 'directory', path: '2026', size_bytes: null, row_count: null, source: 'local', provider: 'local' },
  { name: 'accounts.csv', kind: 'file', path: 'accounts.csv', size_bytes: 2048, row_count: 100, source: 'local', provider: 'local' },
]

const accountsPreview: InputFilePreview = {
  filename: 'accounts.csv',
  header: ['Name', 'ExternalId__c', 'BillingCity'],
  rows: [
    { Name: 'Acme Corp', ExternalId__c: 'ACCT-001', BillingCity: 'New York' },
    { Name: 'Globex', ExternalId__c: 'ACCT-002', BillingCity: 'Springfield' },
  ],
  total_rows: 150,
  filtered_rows: null,
  offset: 0,
  limit: 50,
  has_next: true,
}

const widePreview: InputFilePreview = {
  filename: 'wide.csv',
  header: Array.from({ length: 20 }, (_, i) => `Column${i + 1}`),
  rows: [Object.fromEntries(Array.from({ length: 20 }, (_, i) => [`Column${i + 1}`, `val${i}`]))],
  total_rows: 1,
  filtered_rows: null,
  offset: 0,
  limit: 50,
  has_next: false,
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
    vi.mocked(inputConnectionsApi.list).mockResolvedValue([])
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
    expect(filesApi.previewInput).toHaveBeenCalledWith(
      'accounts.csv',
      { offset: 0, limit: 50, filters: [] },
      'local',
    )
  })

  it('shows panel loading indicator while preview is fetching', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockReturnValue(new Promise(() => {}))
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
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
      expect(screen.getByText('Preview failed')).toBeInTheDocument()
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

  it('shows the preview filename label', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockResolvedValue(accountsPreview)
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => {
      expect(screen.getAllByText('accounts.csv')[0]).toBeInTheDocument()
    })
  })

  it('shows pagination totals from the shared preview panel', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockResolvedValue(accountsPreview)
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => {
      expect(screen.getByText('Page 1 of 3 (150 rows)')).toBeInTheDocument()
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

  it('does not show the legacy truncation note', async () => {
    const user = userEvent.setup()
    const fullPreview: InputFilePreview = { ...accountsPreview, total_rows: 2, has_next: false }
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockResolvedValue(fullPreview)
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => screen.getByText('Page 1 of 1 (2 rows)'))
    expect(screen.queryByText(/Showing first/)).not.toBeInTheDocument()
  })

  it('paginates through the selected file with CsvPreviewPanel', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockImplementation(async (_filePath, params) => {
      if (params?.offset === 50) {
        return {
          ...accountsPreview,
          rows: [{ Name: 'Initech', ExternalId__c: 'ACCT-051', BillingCity: 'Austin' }],
          offset: 50,
          has_next: true,
        }
      }
      return accountsPreview
    })

    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() =>
      expect(filesApi.previewInput).toHaveBeenCalledWith(
        'accounts.csv',
        { offset: 0, limit: 50, filters: [] },
        'local',
      ),
    )

    await user.click(screen.getByRole('button', { name: 'Next page' }))

    await waitFor(() =>
      expect(filesApi.previewInput).toHaveBeenCalledWith(
        'accounts.csv',
        { offset: 50, limit: 50, filters: [] },
        'local',
      ),
    )
  })

  it('applies filters through the shared preview panel', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    vi.mocked(filesApi.previewInput).mockResolvedValue(accountsPreview)

    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => screen.getByText('Acme Corp'))

    await user.click(screen.getByText('+ Add Filter'))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter column' }), 'Name')
    await user.type(screen.getByRole('textbox', { name: 'Filter value' }), 'Acme')
    await user.click(screen.getByRole('button', { name: 'Apply' }))

    await waitFor(() =>
      expect(filesApi.previewInput).toHaveBeenLastCalledWith(
        'accounts.csv',
        { offset: 0, limit: 50, filters: [{ column: 'Name', value: 'Acme' }] },
        'local',
      ),
    )
  })

  // ── Horizontal scroll ──────────────────────────────────────────────────────

  it('wraps the preview table in an overflow-x-auto container', async () => {
    const user = userEvent.setup()
    vi.mocked(filesApi.listInput).mockResolvedValue([
      { name: 'wide.csv', kind: 'file', path: 'wide.csv', size_bytes: 1024, row_count: 1, source: 'local', provider: 'local' },
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
      { name: 'wide.csv', kind: 'file', path: 'wide.csv', size_bytes: 1024, row_count: 1, source: 'local', provider: 'local' },
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
      total_rows: 1,
      filtered_rows: null,
      offset: 0,
      limit: 50,
      has_next: false,
    }

    vi.mocked(filesApi.previewInput)
      .mockResolvedValueOnce(accountsPreview)
      .mockResolvedValueOnce(contactsPreview)

    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))

    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => screen.getByRole('columnheader', { name: 'Name' }))

    await user.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() =>
      expect(filesApi.previewInput).toHaveBeenCalledWith(
        'accounts.csv',
        { offset: 50, limit: 50, filters: [] },
        'local',
      ),
    )

    await user.click(screen.getByText('contacts.csv'))
    await waitFor(() => {
      expect(screen.getByRole('columnheader', { name: 'FirstName' })).toBeInTheDocument()
      expect(screen.getByText('jane@example.com')).toBeInTheDocument()
    })
    expect(filesApi.previewInput).toHaveBeenLastCalledWith(
      'contacts.csv',
      { offset: 0, limit: 50, filters: [] },
      'local',
    )
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
    expect(filesApi.listInput).toHaveBeenCalledWith('2026', 'local')
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
    expect(filesApi.listInput).toHaveBeenCalledWith('', 'local')
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

  // ── Source selector ────────────────────────────────────────────────────────

  it('does not show source selector when no input connections exist', async () => {
    vi.mocked(inputConnectionsApi.list).mockResolvedValue([])
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    expect(screen.queryByLabelText('Source')).not.toBeInTheDocument()
  })

  it('shows source selector when input connections exist', async () => {
    vi.mocked(inputConnectionsApi.list).mockResolvedValue([s3Connection])
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    renderFilesPage()
    await waitFor(() => {
      expect(screen.getByLabelText('Source')).toBeInTheDocument()
    })
  })

  it('lists local and S3 connection options in source selector', async () => {
    vi.mocked(inputConnectionsApi.list).mockResolvedValue([s3Connection])
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    renderFilesPage()
    await waitFor(() => screen.getByLabelText('Source'))
    const select = screen.getByLabelText('Source')
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
    expect(options).toEqual(['Local files', 'Production S3'])
  })

  it('defaults to local source', async () => {
    vi.mocked(inputConnectionsApi.list).mockResolvedValue([s3Connection])
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    renderFilesPage()
    await waitFor(() => screen.getByLabelText('Source'))
    expect(screen.getByLabelText<HTMLSelectElement>('Source').value).toBe('local')
  })

  it('calls listInput with the connection id when source is changed', async () => {
    const user = userEvent.setup()
    vi.mocked(inputConnectionsApi.list).mockResolvedValue([s3Connection])
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    renderFilesPage()
    await waitFor(() => screen.getByLabelText('Source'))
    await user.selectOptions(screen.getByLabelText('Source'), 'conn-s3-1')
    expect(filesApi.listInput).toHaveBeenCalledWith('', 'conn-s3-1')
  })

  it('resets path to root when source changes', async () => {
    const user = userEvent.setup()
    vi.mocked(inputConnectionsApi.list).mockResolvedValue([s3Connection])
    vi.mocked(filesApi.listInput).mockResolvedValue(mixedList)
    renderFilesPage()
    await waitFor(() => screen.getByText('2026'))
    // navigate into subdirectory
    await user.click(screen.getByText('2026'))
    await waitFor(() => {
      expect(screen.getByRole('navigation', { name: 'Directory breadcrumb' })).toHaveTextContent('2026')
    })
    // switch source — path should reset to root
    vi.mocked(filesApi.listInput).mockResolvedValue(fileList)
    await user.selectOptions(screen.getByLabelText('Source'), 'conn-s3-1')
    await waitFor(() => {
      expect(filesApi.listInput).toHaveBeenCalledWith('', 'conn-s3-1')
    })
    expect(screen.getByRole('navigation', { name: 'Directory breadcrumb' })).not.toHaveTextContent('2026')
  })

  it('clears selected file when source changes', async () => {
    const user = userEvent.setup()
    const s3Files: InputDirectoryEntry[] = [
      { name: 'remote.csv', kind: 'file', path: 'remote.csv', size_bytes: 1024, row_count: 10 },
    ]
    vi.mocked(inputConnectionsApi.list).mockResolvedValue([s3Connection])
    vi.mocked(filesApi.listInput).mockResolvedValueOnce(fileList).mockResolvedValue(s3Files)
    vi.mocked(filesApi.previewInput).mockReturnValue(new Promise(() => {}))
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    expect(screen.queryByText('No file selected')).not.toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Source'), 'conn-s3-1')
    await waitFor(() => {
      expect(screen.getByText('No file selected')).toBeInTheDocument()
    })
  })

  it('restarts preview state when source changes and a new file is selected', async () => {
    const user = userEvent.setup()
    const s3Files: InputDirectoryEntry[] = [
      { name: 'remote.csv', kind: 'file', path: 'remote.csv', size_bytes: 1024, row_count: 10 },
    ]
    vi.mocked(inputConnectionsApi.list).mockResolvedValue([s3Connection])
    vi.mocked(filesApi.listInput).mockResolvedValueOnce(fileList).mockResolvedValue(s3Files)
    vi.mocked(filesApi.previewInput).mockImplementation(async (filePath, params) => ({
      ...(filePath === 'remote.csv'
        ? {
            ...accountsPreview,
            filename: 'remote.csv',
            total_rows: 10,
            has_next: false,
            rows: [{ Name: 'Remote Row', ExternalId__c: 'ACCT-900', BillingCity: 'London' }],
          }
        : accountsPreview),
      ...(typeof params === 'object' && params != null ? { offset: params.offset, limit: params.limit } : {}),
    }))

    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    await waitFor(() => screen.getByText('Acme Corp'))

    await user.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() =>
      expect(filesApi.previewInput).toHaveBeenCalledWith(
        'accounts.csv',
        { offset: 50, limit: 50, filters: [] },
        'local',
      ),
    )

    await user.selectOptions(screen.getByLabelText('Source'), 'conn-s3-1')
    await waitFor(() => screen.getByText('remote.csv'))
    await user.click(screen.getByText('remote.csv'))

    await waitFor(() =>
      expect(filesApi.previewInput).toHaveBeenLastCalledWith(
        'remote.csv',
        { offset: 0, limit: 50, filters: [] },
        'conn-s3-1',
      ),
    )
  })

  it('shows generic empty state description for S3 source', async () => {
    const user = userEvent.setup()
    vi.mocked(inputConnectionsApi.list).mockResolvedValue([s3Connection])
    vi.mocked(filesApi.listInput).mockResolvedValueOnce(fileList).mockResolvedValue([])
    renderFilesPage()
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.selectOptions(screen.getByLabelText('Source'), 'conn-s3-1')
    await waitFor(() => {
      expect(screen.getByText('No files found in this location.')).toBeInTheDocument()
    })
  })

  it('shows /data/input description for local empty state', async () => {
    vi.mocked(inputConnectionsApi.list).mockResolvedValue([s3Connection])
    vi.mocked(filesApi.listInput).mockResolvedValue([])
    renderFilesPage()
    await waitFor(() => {
      expect(screen.getByText(/Place CSV files in the \/data\/input directory/)).toBeInTheDocument()
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
    expect(filesApi.previewInput).toHaveBeenCalledWith(
      '2026/accounts.csv',
      { offset: 0, limit: 50, filters: [] },
      'local',
    )
  })
})
