import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { render } from '../utils'
import Connections from '../../pages/Connections'

// ─── Mock the endpoints module ─────────────────────────────────────────────────

vi.mock('../../api/endpoints', () => ({
  connectionsApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    test: vi.fn(),
  },
}))

// Import after mocking so we get the mocked versions
import { connectionsApi } from '../../api/endpoints'

// ─── Test fixtures ─────────────────────────────────────────────────────────────

const conn1 = {
  id: 'conn-1',
  name: 'Production Org',
  instance_url: 'https://prod.my.salesforce.com',
  login_url: 'https://login.salesforce.com',
  client_id: 'clientabc',
  username: 'admin@prod.com',
  is_sandbox: false,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
}

const conn2 = {
  id: 'conn-2',
  name: 'Dev Sandbox',
  instance_url: 'https://dev.sandbox.salesforce.com',
  login_url: 'https://test.salesforce.com',
  client_id: 'clientxyz',
  username: 'admin@sandbox.com',
  is_sandbox: true,
  created_at: '2024-03-02T00:00:00Z',
  updated_at: '2024-03-02T00:00:00Z',
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

function mockList(data: typeof conn1[]) {
  vi.mocked(connectionsApi.list).mockResolvedValue(data)
}

// ─── Tests ─────────────────────────────────────────────────────────────────────

describe('Connections page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ── Loading / empty / error states ─────────────────────────────────────────

  it('shows a loading spinner while fetching', () => {
    vi.mocked(connectionsApi.list).mockReturnValue(new Promise(() => {}))
    render(<Connections />)
    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
  })

  it('shows empty state when list returns no connections', async () => {
    mockList([])
    render(<Connections />)
    await waitFor(() => {
      expect(screen.getByText('No connections yet')).toBeInTheDocument()
    })
  })

  it('shows an error message when the list request fails', async () => {
    vi.mocked(connectionsApi.list).mockRejectedValue(new Error('Network error'))
    render(<Connections />)
    await waitFor(() => {
      expect(screen.getByText(/Failed to load connections/)).toBeInTheDocument()
      expect(screen.getByText(/Network error/)).toBeInTheDocument()
    })
  })

  // ── List rendering ─────────────────────────────────────────────────────────

  it('renders connection names and usernames', async () => {
    mockList([conn1, conn2])
    render(<Connections />)
    await waitFor(() => {
      expect(screen.getByText('Production Org')).toBeInTheDocument()
      expect(screen.getByText('admin@prod.com')).toBeInTheDocument()
      expect(screen.getByText('Dev Sandbox')).toBeInTheDocument()
      expect(screen.getByText('admin@sandbox.com')).toBeInTheDocument()
    })
  })

  it('shows Production badge for non-sandbox and Sandbox badge for sandbox', async () => {
    mockList([conn1, conn2])
    render(<Connections />)
    await waitFor(() => {
      expect(screen.getByText('Production')).toBeInTheDocument()
      expect(screen.getByText('Sandbox')).toBeInTheDocument()
    })
  })

  it('renders Edit, Test, and Delete buttons for each connection', async () => {
    mockList([conn1, conn2])
    render(<Connections />)
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: 'Edit' })).toHaveLength(2)
      expect(screen.getAllByRole('button', { name: 'Test' })).toHaveLength(2)
      expect(screen.getAllByRole('button', { name: 'Delete' })).toHaveLength(2)
    })
  })

  // ── Create flow ─────────────────────────────────────────────────────────────

  it('opens the create modal when "New Connection" is clicked', async () => {
    mockList([])
    render(<Connections />)
    await waitFor(() => screen.getByText('No connections yet'))

    await userEvent.click(screen.getByRole('button', { name: 'New Connection' }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('New Connection')).toBeInTheDocument()
  })

  it('opens the create modal from the empty-state "Add Connection" button', async () => {
    mockList([])
    render(<Connections />)
    await waitFor(() => screen.getByText('No connections yet'))

    await userEvent.click(screen.getByRole('button', { name: 'Add Connection' }))

    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('calls connectionsApi.create with form data and closes modal on success', async () => {
    const user = userEvent.setup()
    mockList([])
    vi.mocked(connectionsApi.create).mockResolvedValue(conn1)

    render(<Connections />)
    await waitFor(() => screen.getByText('No connections yet'))

    await user.click(screen.getByRole('button', { name: 'New Connection' }))

    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText(/Name/), 'Production Org')
    await user.type(within(dialog).getByLabelText(/Username/), 'admin@prod.com')
    await user.type(
      within(dialog).getByLabelText(/Instance URL/),
      'https://prod.my.salesforce.com',
    )
    await user.type(within(dialog).getByLabelText(/Consumer Key/), 'clientabc')
    await user.type(
      within(dialog).getByLabelText(/Private Key/),
      '-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----',
    )

    await user.click(within(dialog).getByRole('button', { name: 'Create Connection' }))

    await waitFor(() => {
      expect(connectionsApi.create).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Production Org',
          username: 'admin@prod.com',
          instance_url: 'https://prod.my.salesforce.com',
          client_id: 'clientabc',
        }),
      )
    })

    // Modal should close
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
  })

  it('displays validation errors returned by the API', async () => {
    const user = userEvent.setup()
    mockList([])
    vi.mocked(connectionsApi.create).mockRejectedValue(
      Object.assign(new Error('Validation error'), {
        name: 'ApiError',
        status: 422,
        detail: [{ type: 'missing', loc: ['body', 'name'], msg: 'Field required', input: null }],
      }),
    )

    render(<Connections />)
    await waitFor(() => screen.getByText('No connections yet'))
    await user.click(screen.getByRole('button', { name: 'New Connection' }))

    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Create Connection' }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
  })

  it('cancels the modal without calling create', async () => {
    const user = userEvent.setup()
    mockList([])
    render(<Connections />)
    await waitFor(() => screen.getByText('No connections yet'))

    await user.click(screen.getByRole('button', { name: 'New Connection' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(connectionsApi.create).not.toHaveBeenCalled()
  })

  // ── Edit flow ───────────────────────────────────────────────────────────────

  it('opens the edit modal pre-filled with connection data', async () => {
    const user = userEvent.setup()
    mockList([conn1])
    render(<Connections />)
    await waitFor(() => screen.getByText('Production Org'))

    await user.click(screen.getAllByRole('button', { name: 'Edit' })[0])

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByDisplayValue('Production Org')).toBeInTheDocument()
    expect(within(dialog).getByDisplayValue('admin@prod.com')).toBeInTheDocument()
    expect(within(dialog).getByText('Edit Connection')).toBeInTheDocument()
  })

  it('private key field is blank and not required when editing', async () => {
    const user = userEvent.setup()
    mockList([conn1])
    render(<Connections />)
    await waitFor(() => screen.getByText('Production Org'))

    await user.click(screen.getAllByRole('button', { name: 'Edit' })[0])

    const keyField = screen.getByLabelText(/Private Key/)
    expect(keyField).toHaveValue('')
    expect(keyField).not.toBeRequired()
  })

  it('calls connectionsApi.update without private_key when field left blank', async () => {
    const user = userEvent.setup()
    mockList([conn1])
    vi.mocked(connectionsApi.update).mockResolvedValue(conn1)

    render(<Connections />)
    await waitFor(() => screen.getByText('Production Org'))

    await user.click(screen.getAllByRole('button', { name: 'Edit' })[0])

    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Save Changes' }))

    await waitFor(() => {
      expect(connectionsApi.update).toHaveBeenCalledWith(
        'conn-1',
        expect.not.objectContaining({ private_key: expect.anything() }),
      )
    })
  })

  it('includes private_key in update when field is filled in', async () => {
    const user = userEvent.setup()
    mockList([conn1])
    vi.mocked(connectionsApi.update).mockResolvedValue(conn1)

    render(<Connections />)
    await waitFor(() => screen.getByText('Production Org'))

    await user.click(screen.getAllByRole('button', { name: 'Edit' })[0])

    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText(/Private Key/), 'new-pem-key')
    await user.click(within(dialog).getByRole('button', { name: 'Save Changes' }))

    await waitFor(() => {
      expect(connectionsApi.update).toHaveBeenCalledWith(
        'conn-1',
        expect.objectContaining({ private_key: 'new-pem-key' }),
      )
    })
  })

  // ── Delete flow ─────────────────────────────────────────────────────────────

  it('opens delete confirmation modal with connection name', async () => {
    const user = userEvent.setup()
    mockList([conn1])
    render(<Connections />)
    await waitFor(() => screen.getByText('Production Org'))

    await user.click(screen.getAllByRole('button', { name: 'Delete' })[0])

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Delete Connection')).toBeInTheDocument()
    expect(within(dialog).getByText('Production Org')).toBeInTheDocument()
  })

  it('calls connectionsApi.delete when confirmed', async () => {
    const user = userEvent.setup()
    mockList([conn1])
    vi.mocked(connectionsApi.delete).mockResolvedValue(undefined)

    render(<Connections />)
    await waitFor(() => screen.getByText('Production Org'))

    // Open the delete confirmation modal
    await user.click(screen.getAllByRole('button', { name: 'Delete' })[0])

    // Wait for dialog to appear
    const dialog = await screen.findByRole('dialog')

    // Click the danger Delete button inside the dialog (last Delete button in dialog)
    const deleteButtons = within(dialog).getAllByRole('button', { name: 'Delete' })
    await user.click(deleteButtons[deleteButtons.length - 1])

    await waitFor(() => {
      expect(connectionsApi.delete).toHaveBeenCalledWith('conn-1')
    })
  })

  it('closes delete modal without deleting when Cancel is clicked', async () => {
    const user = userEvent.setup()
    mockList([conn1])
    render(<Connections />)
    await waitFor(() => screen.getByText('Production Org'))

    await user.click(screen.getAllByRole('button', { name: 'Delete' })[0])

    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(connectionsApi.delete).not.toHaveBeenCalled()
  })

  // ── Test connection flow ────────────────────────────────────────────────────

  it('shows success result panel after a successful test', async () => {
    const user = userEvent.setup()
    mockList([conn1])
    vi.mocked(connectionsApi.test).mockResolvedValue({
      success: true,
      message: 'Connected successfully',
      instance_url: 'https://prod.my.salesforce.com',
    })

    render(<Connections />)
    await waitFor(() => screen.getByText('Production Org'))

    await user.click(screen.getAllByRole('button', { name: 'Test' })[0])

    await waitFor(() => {
      expect(screen.getByRole('status')).toBeInTheDocument()
      expect(screen.getByText(/Connection successful/)).toBeInTheDocument()
      expect(screen.getByText('Connected successfully')).toBeInTheDocument()
    })
  })

  it('shows failure result panel when test returns success: false', async () => {
    const user = userEvent.setup()
    mockList([conn1])
    vi.mocked(connectionsApi.test).mockResolvedValue({
      success: false,
      message: 'Authentication failed: invalid key',
    })

    render(<Connections />)
    await waitFor(() => screen.getByText('Production Org'))

    await user.click(screen.getAllByRole('button', { name: 'Test' })[0])

    await waitFor(() => {
      expect(screen.getByText(/Connection failed/)).toBeInTheDocument()
      expect(screen.getByText('Authentication failed: invalid key')).toBeInTheDocument()
    })
  })

  it('shows the instance_url in the result panel when provided', async () => {
    const user = userEvent.setup()
    // Use a distinct URL that won't appear in the table
    const distinctUrl = 'https://uniquetest99.my.salesforce.com'
    mockList([conn1])
    vi.mocked(connectionsApi.test).mockResolvedValue({
      success: true,
      message: 'OK',
      instance_url: distinctUrl,
    })

    render(<Connections />)
    await waitFor(() => screen.getByText('Production Org'))

    await user.click(screen.getAllByRole('button', { name: 'Test' })[0])

    await waitFor(() => {
      const statusPanel = screen.getByRole('status')
      expect(within(statusPanel).getByText(distinctUrl)).toBeInTheDocument()
    })
  })

  it('dismisses the test result panel when × is clicked', async () => {
    const user = userEvent.setup()
    mockList([conn1])
    vi.mocked(connectionsApi.test).mockResolvedValue({
      success: true,
      message: 'Connected successfully',
    })

    render(<Connections />)
    await waitFor(() => screen.getByText('Production Org'))

    await user.click(screen.getAllByRole('button', { name: 'Test' })[0])
    await waitFor(() => screen.getByRole('status'))

    await user.click(screen.getByRole('button', { name: 'Dismiss test result' }))

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('clears previous test result when a new test is run', async () => {
    const user = userEvent.setup()
    mockList([conn1, conn2])
    vi.mocked(connectionsApi.test)
      .mockResolvedValueOnce({ success: true, message: 'First result' })
      .mockResolvedValueOnce({ success: false, message: 'Second result' })

    render(<Connections />)
    await waitFor(() => screen.getByText('Production Org'))

    await user.click(screen.getAllByRole('button', { name: 'Test' })[0])
    await waitFor(() => screen.getByText('First result'))

    await user.click(screen.getAllByRole('button', { name: 'Test' })[1])
    await waitFor(() => screen.getByText('Second result'))

    expect(screen.queryByText('First result')).not.toBeInTheDocument()
  })

  // ── Login URL / sandbox toggle ──────────────────────────────────────────────

  it('auto-checks sandbox when test.salesforce.com is selected', async () => {
    const user = userEvent.setup()
    mockList([])
    render(<Connections />)
    await waitFor(() => screen.getByText('No connections yet'))

    await user.click(screen.getByRole('button', { name: 'New Connection' }))

    const loginUrlSelect = screen.getByLabelText(/Login URL/)
    await user.selectOptions(loginUrlSelect, 'https://test.salesforce.com')

    const sandboxCheckbox = screen.getByLabelText(/Sandbox org/)
    expect(sandboxCheckbox).toBeChecked()
  })

  it('auto-unchecks sandbox when login.salesforce.com is selected', async () => {
    const user = userEvent.setup()
    mockList([])
    render(<Connections />)
    await waitFor(() => screen.getByText('No connections yet'))

    await user.click(screen.getByRole('button', { name: 'New Connection' }))

    // Select sandbox first, then switch back
    const loginUrlSelect = screen.getByLabelText(/Login URL/)
    await user.selectOptions(loginUrlSelect, 'https://test.salesforce.com')
    await user.selectOptions(loginUrlSelect, 'https://login.salesforce.com')

    const sandboxCheckbox = screen.getByLabelText(/Sandbox org/)
    expect(sandboxCheckbox).not.toBeChecked()
  })
})
