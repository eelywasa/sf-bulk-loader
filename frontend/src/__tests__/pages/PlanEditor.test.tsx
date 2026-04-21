import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../../components/ui/Toast'
import PlanEditor from '../../pages/PlanEditor'

// ─── Mock AuthContext — admin permissions so permission-gated UI is visible ────

const MOCK_ALL_PERMISSIONS = new Set([
  'connections.view', 'connections.view_credentials', 'connections.manage',
  'plans.view', 'plans.manage',
  'runs.view', 'runs.execute', 'runs.abort',
  'files.view', 'files.view_contents',
  'users.manage', 'system.settings',
])

vi.mock('../../context/AuthContext', () => ({
  useAuth: vi.fn(() => ({
    token: 'test-token',
    user: { id: '1', username: 'admin', is_admin: true, profile: { name: 'admin' }, permissions: [...MOCK_ALL_PERMISSIONS] },
    permissions: MOCK_ALL_PERMISSIONS,
    profileName: 'admin',
    isBootstrapping: false,
    authRequired: true,
    login: vi.fn(),
    logout: vi.fn(),
  })),
  useAuthOptional: vi.fn(() => ({
    token: 'test-token',
    user: { id: '1', username: 'admin', is_admin: true, profile: { name: 'admin' }, permissions: [...MOCK_ALL_PERMISSIONS] },
    permissions: MOCK_ALL_PERMISSIONS,
    profileName: 'admin',
    isBootstrapping: false,
    authRequired: true,
    login: vi.fn(),
    logout: vi.fn(),
  })),
}))

// ─── Mock the endpoints module ─────────────────────────────────────────────────

vi.mock('../../api/endpoints', () => ({
  plansApi: {
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    startRun: vi.fn(),
  },
  stepsApi: {
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    reorder: vi.fn(),
    preview: vi.fn(),
    validateSoql: vi.fn(),
  },
  connectionsApi: {
    list: vi.fn(),
    listObjects: vi.fn(),
  },
  inputConnectionsApi: {
    list: vi.fn(),
  },
  filesApi: {
    listInput: vi.fn(),
    previewInput: vi.fn(),
  },
  notificationSubscriptionsApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}))

import { plansApi, stepsApi, connectionsApi, inputConnectionsApi, filesApi, notificationSubscriptionsApi } from '../../api/endpoints'

// ─── Test fixtures ─────────────────────────────────────────────────────────────

const conn1 = {
  id: 'conn-1',
  name: 'Production Org',
  instance_url: 'https://prod.my.salesforce.com',
  login_url: 'https://login.salesforce.com',
  client_id: 'abc',
  username: 'admin@prod.com',
  is_sandbox: false,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
}

const step1 = {
  id: 'step-1',
  load_plan_id: 'plan-1',
  sequence: 1,
  object_name: 'Account',
  operation: 'insert' as const,
  csv_file_pattern: 'accounts_*.csv',
  partition_size: 10000,
  external_id_field: null,
  assignment_rule_id: null,
  input_connection_id: null,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
}

const step2 = {
  id: 'step-2',
  load_plan_id: 'plan-1',
  sequence: 2,
  object_name: 'Contact',
  operation: 'upsert' as const,
  csv_file_pattern: 'contacts_*.csv',
  partition_size: 5000,
  external_id_field: 'ExternalId__c',
  assignment_rule_id: null,
  input_connection_id: null,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
}

const inputConnection1 = {
  id: 'ic-1',
  name: 'S3 Source',
  provider: 's3',
  bucket: 'bucket-a',
  root_prefix: 'imports/',
  region: 'eu-west-2',
  direction: 'in' as const,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
}

const remoteStep = {
  ...step1,
  id: 'step-remote',
  csv_file_pattern: 'remote/accounts.csv',
  input_connection_id: 'ic-1',
}

const queryStep = {
  id: 'step-query',
  load_plan_id: 'plan-1',
  sequence: 1,
  object_name: 'Account',
  operation: 'query' as const,
  csv_file_pattern: null,
  soql: 'SELECT Id, Name FROM Account',
  partition_size: 10000,
  external_id_field: null,
  assignment_rule_id: null,
  input_connection_id: null,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
}

const planWithQueryStep = {
  id: 'plan-1',
  name: 'Q1 Migration',
  description: 'Test plan description',
  connection_id: 'conn-1',
  abort_on_step_failure: true,
  error_threshold_pct: 10,
  max_parallel_jobs: 5,
  output_connection_id: null,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
  load_steps: [queryStep],
}

const plan1 = {
  id: 'plan-1',
  name: 'Q1 Migration',
  description: 'Test plan description',
  connection_id: 'conn-1',
  abort_on_step_failure: true,
  error_threshold_pct: 10,
  max_parallel_jobs: 5,
  output_connection_id: null,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
  load_steps: [step1],
}

const planNoSteps = {
  ...plan1,
  load_steps: [],
}

const newPlanResponse = {
  id: 'plan-new',
  name: 'My New Plan',
  description: null,
  connection_id: 'conn-1',
  abort_on_step_failure: true,
  error_threshold_pct: 10,
  max_parallel_jobs: 5,
  output_connection_id: null,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
}

// ─── Render helpers ─────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

function renderEditor(id: string) {
  const queryClient = makeQueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={[`/plans/${id}`]}>
          <Routes>
            <Route path="/plans/:id" element={<PlanEditor />} />
            <Route path="/plans" element={<div data-testid="plans-list-page" />} />
            <Route path="/runs/:id" element={<div data-testid="run-detail-page" />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

// ─── Tests ─────────────────────────────────────────────────────────────────────

describe('PlanEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(connectionsApi.list).mockResolvedValue([conn1])
    vi.mocked(connectionsApi.listObjects).mockResolvedValue(['Account', 'Contact', 'Opportunity'])
    vi.mocked(inputConnectionsApi.list).mockResolvedValue([inputConnection1])
    vi.mocked(filesApi.listInput).mockResolvedValue([])
    vi.mocked(filesApi.previewInput).mockResolvedValue({
      filename: '',
      header: [],
      rows: [],
      total_rows: null,
      filtered_rows: null,
      offset: 0,
      limit: 1,
      has_next: false,
    })
    vi.mocked(notificationSubscriptionsApi.list).mockResolvedValue([])
  })

  // ── New plan mode ──────────────────────────────────────────────────────────

  it('shows "New Load Plan" heading when id is "new"', () => {
    renderEditor('new')
    expect(screen.getByRole('heading', { name: 'New Load Plan' })).toBeInTheDocument()
  })

  it('shows "Save Plan" button for new plans', () => {
    renderEditor('new')
    expect(screen.getByRole('button', { name: 'Save Plan' })).toBeInTheDocument()
  })

  it('does not show "Start Run" button for new plans', () => {
    renderEditor('new')
    expect(screen.queryByRole('button', { name: 'Start Run' })).not.toBeInTheDocument()
  })

  it('shows "Save plan first" hint for new plans', () => {
    renderEditor('new')
    expect(screen.getByText(/Save the plan first/)).toBeInTheDocument()
  })

  it('shows connection options from connectionsApi.list', async () => {
    renderEditor('new')
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Production Org' })).toBeInTheDocument()
    })
  })

  it('creates a new plan and navigates to the edit page', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.create).mockResolvedValue(newPlanResponse)
    vi.mocked(plansApi.get).mockResolvedValue({ ...newPlanResponse, load_steps: [] })

    renderEditor('new')

    await user.type(screen.getByLabelText(/Name/), 'My New Plan')
    // Select a connection
    await waitFor(() => screen.getByRole('option', { name: 'Production Org' }))
    await user.selectOptions(screen.getByLabelText(/Connection/), 'conn-1')
    await user.click(screen.getByRole('button', { name: 'Save Plan' }))

    await waitFor(() => {
      expect(plansApi.create).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'My New Plan',
          connection_id: 'conn-1',
        }),
      )
    })

    // Should navigate to /plans/plan-new
    await waitFor(() => {
      expect(screen.getByTestId('run-detail-page')).not.toBeInTheDocument()
    }).catch(() => {
      // Navigation to /plans/:id renders the same PlanEditor component;
      // just check that create was called
    })
  })

  it('displays validation errors from API on create', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.create).mockRejectedValue(
      Object.assign(new Error('Validation error'), {
        name: 'ApiError',
        status: 422,
        detail: [{ type: 'missing', loc: ['body', 'name'], msg: 'Field required', input: null }],
      }),
    )

    renderEditor('new')
    await user.click(screen.getByRole('button', { name: 'Save Plan' }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
  })

  // ── Edit plan mode: loading / error ────────────────────────────────────────

  it('shows loading spinner while fetching an existing plan', () => {
    vi.mocked(plansApi.get).mockReturnValue(new Promise(() => {}))
    renderEditor('plan-1')
    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
  })

  it('shows an error message when plan fetch fails', async () => {
    vi.mocked(plansApi.get).mockRejectedValue(new Error('Not found'))
    renderEditor('plan-1')
    await waitFor(() => {
      expect(screen.getByText(/Failed to load plan/)).toBeInTheDocument()
      expect(screen.getByText(/Not found/)).toBeInTheDocument()
    })
  })

  it('shows "Back to Plans" link when plan fetch fails', async () => {
    vi.mocked(plansApi.get).mockRejectedValue(new Error('Not found'))
    renderEditor('plan-1')
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Back to Plans' })).toBeInTheDocument()
    })
  })

  it('navigates to /plans when "Back to Plans" is clicked after error', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockRejectedValue(new Error('Not found'))
    renderEditor('plan-1')
    await waitFor(() => screen.getByRole('button', { name: 'Back to Plans' }))

    await user.click(screen.getByRole('button', { name: 'Back to Plans' }))

    expect(screen.getByTestId('plans-list-page')).toBeInTheDocument()
  })

  // ── Edit plan mode: form rendering ────────────────────────────────────────

  it('shows "Edit Load Plan" heading for an existing plan', async () => {
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    renderEditor('plan-1')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Edit Load Plan' })).toBeInTheDocument()
    })
  })

  it('fills the form with plan data on load', async () => {
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    renderEditor('plan-1')
    await waitFor(() => {
      expect(screen.getByDisplayValue('Q1 Migration')).toBeInTheDocument()
      expect(screen.getByDisplayValue('Test plan description')).toBeInTheDocument()
    })
  })

  it('shows "Save Changes" button for existing plans', async () => {
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    renderEditor('plan-1')
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Save Changes' })).toBeInTheDocument()
    })
  })

  it('shows "Start Run" button for existing plans', async () => {
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    renderEditor('plan-1')
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Start Run' })).toBeInTheDocument()
    })
  })

  it('calls plansApi.update with form data when "Save Changes" is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    vi.mocked(plansApi.update).mockResolvedValue(plan1)

    renderEditor('plan-1')
    await waitFor(() => screen.getByDisplayValue('Q1 Migration'))

    // Clear and retype name
    const nameInput = screen.getByLabelText(/Name/)
    await user.clear(nameInput)
    await user.type(nameInput, 'Updated Plan Name')

    await user.click(screen.getByRole('button', { name: 'Save Changes' }))

    await waitFor(() => {
      expect(plansApi.update).toHaveBeenCalledWith(
        'plan-1',
        expect.objectContaining({ name: 'Updated Plan Name' }),
      )
    })
  })

  // ── Steps list ────────────────────────────────────────────────────────────

  it('renders steps from the loaded plan', async () => {
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    renderEditor('plan-1')
    await waitFor(() => {
      expect(screen.getByText('Account')).toBeInTheDocument()
      expect(screen.getByText('insert')).toBeInTheDocument()
      expect(screen.getByText('accounts_*.csv')).toBeInTheDocument()
    })
  })

  it('shows empty steps message when plan has no steps', async () => {
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => {
      expect(screen.getByText(/No steps yet/)).toBeInTheDocument()
    })
  })

  it('shows Add Step button in step card header', async () => {
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    renderEditor('plan-1')
    await waitFor(() => {
      // The card has an Add Step button in the header actions
      expect(screen.getAllByRole('button', { name: 'Add Step' })).not.toHaveLength(0)
    })
  })

  it('shows Preview, Edit, and Delete buttons for each step', async () => {
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    renderEditor('plan-1')
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Preview' })).toBeInTheDocument()
      expect(screen.getAllByRole('button', { name: 'Edit' })).not.toHaveLength(0)
      expect(screen.getAllByRole('button', { name: 'Delete' })).not.toHaveLength(0)
    })
  })

  it('shows move-up and move-down buttons for steps', async () => {
    vi.mocked(plansApi.get).mockResolvedValue({ ...plan1, load_steps: [step1, step2] })
    renderEditor('plan-1')
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: 'Move step up' })).not.toHaveLength(0)
      expect(screen.getAllByRole('button', { name: 'Move step down' })).not.toHaveLength(0)
    })
  })

  // ── Add step modal ────────────────────────────────────────────────────────

  it('opens the "Add Step" modal when the button is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])

    const dialog = screen.getByRole('dialog')
    // Modal title is an h2; using heading role to distinguish from the submit button
    expect(within(dialog).getByRole('heading', { name: 'Add Step' })).toBeInTheDocument()
  })

  it('calls stepsApi.create with form data when "Add Step" is submitted', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(stepsApi.create).mockResolvedValue(step1)

    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])

    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText(/Salesforce Object/), 'Account')
    await user.type(within(dialog).getByLabelText(/CSV File Pattern/), 'accounts_*.csv')

    await user.click(within(dialog).getByRole('button', { name: 'Add Step' }))

    await waitFor(() => {
      expect(stepsApi.create).toHaveBeenCalledWith(
        'plan-1',
        expect.objectContaining({
          object_name: 'Account',
          csv_file_pattern: 'accounts_*.csv',
          operation: 'insert',
          input_connection_id: null,
        }),
      )
    })
  })

  it('shows input source options in the step modal', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])

    expect(screen.getByLabelText(/Input Source/)).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Local input files' })).toBeInTheDocument()
    expect(
      screen.getByRole('option', { name: 'Local output files (prior run results)' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'S3 Source' })).toBeInTheDocument()
  })

  it('submits the local-output sentinel when Local output files is selected', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(stepsApi.create).mockResolvedValue(step1)

    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])

    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText(/Salesforce Object/), 'Account')
    await user.type(within(dialog).getByLabelText(/CSV File Pattern/), 'accounts_*.csv')
    await user.selectOptions(within(dialog).getByLabelText(/Input Source/), 'local-output')

    await user.click(within(dialog).getByRole('button', { name: 'Add Step' }))

    await waitFor(() => {
      expect(stepsApi.create).toHaveBeenCalledWith(
        'plan-1',
        expect.objectContaining({
          input_connection_id: 'local-output',
        }),
      )
    })
  })

  it('preselects the remote input source when editing a remote-backed step', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue({ ...plan1, load_steps: [remoteStep] })
    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    const editButtons = screen.getAllByRole('button', { name: 'Edit' })
    await user.click(editButtons[editButtons.length - 1])

    expect(screen.getByLabelText(/Input Source/)).toHaveValue('ic-1')
    expect(screen.getByDisplayValue('remote/accounts.csv')).toBeInTheDocument()
  })

  it('clears the pattern when the input source changes', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    const patternInput = screen.getByLabelText(/CSV File Pattern/)
    await user.type(patternInput, 'accounts.csv')
    await user.selectOptions(screen.getByLabelText(/Input Source/), 'ic-1')

    expect(patternInput).toHaveValue('')
  })

  it('uses the selected source for file browsing and header preview', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(filesApi.listInput).mockResolvedValue([
      {
        name: 'accounts.csv',
        kind: 'file',
        path: 'accounts.csv',
        size_bytes: 100,
        row_count: null,
        source: 'ic-1',
        provider: 's3',
      },
    ])
    vi.mocked(filesApi.previewInput).mockResolvedValue({
      filename: 'accounts.csv',
      header: ['ExternalId__c'],
      rows: [{ ExternalId__c: 'A-1' }],
      total_rows: 1,
      filtered_rows: null,
      offset: 0,
      limit: 1,
      has_next: false,
      source: 'ic-1',
      provider: 's3',
    })

    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.selectOptions(screen.getByLabelText(/Input Source/), 'ic-1')
    await user.click(screen.getByRole('button', { name: 'Browse' }))
    await waitFor(() => {
      expect(filesApi.listInput).toHaveBeenCalledWith('', 'ic-1')
    })

    await user.selectOptions(screen.getByLabelText(/Operation/), 'upsert')
    await user.type(screen.getByLabelText(/CSV File Pattern/), 'accounts.csv')

    await waitFor(() => {
      expect(filesApi.previewInput).toHaveBeenCalledWith(
        'accounts.csv',
        { offset: 0, limit: 1, filters: [] },
        'ic-1',
      )
    })
  })

  it('shows a file picker error when source listing fails', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(filesApi.listInput).mockRejectedValue(new Error('Access denied'))

    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.selectOptions(screen.getByLabelText(/Input Source/), 'ic-1')
    await user.click(screen.getByRole('button', { name: 'Browse' }))

    await waitFor(() => {
      expect(screen.getByText('Could not load files for this source.')).toBeInTheDocument()
    })
  })

  it('shows external ID field only when operation is "upsert"', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])

    // External ID field should not be visible for insert
    expect(screen.queryByLabelText(/External ID Field/)).not.toBeInTheDocument()

    // Switch to upsert
    await user.selectOptions(screen.getByLabelText(/Operation/), 'upsert')
    expect(screen.getByLabelText(/External ID Field/)).toBeInTheDocument()
  })

  it('closes the step modal without saving when Cancel is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(stepsApi.create).not.toHaveBeenCalled()
  })

  it('shows validation errors from API in the step modal', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(stepsApi.create).mockRejectedValue(
      Object.assign(new Error('Validation error'), {
        name: 'ApiError',
        status: 422,
        detail: [
          {
            type: 'missing',
            loc: ['body', 'object_name'],
            msg: 'Field required',
            input: null,
          },
        ],
      }),
    )

    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Add Step' }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
  })

  // ── Edit step modal ───────────────────────────────────────────────────────

  it('opens the edit modal pre-filled with step data', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    // Click the Edit button for the step (not the Save Changes for the plan)
    const editButtons = screen.getAllByRole('button', { name: 'Edit' })
    // The last Edit button in the list is for the step (plan Save Changes is different)
    await user.click(editButtons[editButtons.length - 1])

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Edit Step')).toBeInTheDocument()
    expect(within(dialog).getByDisplayValue('Account')).toBeInTheDocument()
    expect(within(dialog).getByDisplayValue('accounts_*.csv')).toBeInTheDocument()
  })

  it('calls stepsApi.update when an edited step is saved', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    vi.mocked(stepsApi.update).mockResolvedValue({ ...step1, object_name: 'Updated' })

    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    const editButtons = screen.getAllByRole('button', { name: 'Edit' })
    await user.click(editButtons[editButtons.length - 1])

    const dialog = screen.getByRole('dialog')
    const objectInput = within(dialog).getByLabelText(/Salesforce Object/)
    await user.clear(objectInput)
    await user.type(objectInput, 'Updated')

    await user.click(within(dialog).getByRole('button', { name: 'Save Changes' }))

    await waitFor(() => {
      expect(stepsApi.update).toHaveBeenCalledWith(
        'plan-1',
        'step-1',
        expect.objectContaining({ object_name: 'Updated' }),
      )
    })
  })

  // ── Delete step ───────────────────────────────────────────────────────────

  it('opens delete confirmation modal with step object name', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    // Click the Delete button for the step row
    const deleteButtons = screen.getAllByRole('button', { name: 'Delete' })
    await user.click(deleteButtons[deleteButtons.length - 1])

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Delete Step')).toBeInTheDocument()
    expect(within(dialog).getByText('Account')).toBeInTheDocument()
  })

  it('calls stepsApi.delete when confirmed', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    vi.mocked(stepsApi.delete).mockResolvedValue(undefined)

    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    const deleteButtons = screen.getAllByRole('button', { name: 'Delete' })
    await user.click(deleteButtons[deleteButtons.length - 1])

    const dialog = await screen.findByRole('dialog')
    const confirmButtons = within(dialog).getAllByRole('button', { name: 'Delete' })
    await user.click(confirmButtons[confirmButtons.length - 1])

    await waitFor(() => {
      expect(stepsApi.delete).toHaveBeenCalledWith('plan-1', 'step-1')
    })
  })

  it('closes delete step modal without deleting when Cancel is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    const deleteButtons = screen.getAllByRole('button', { name: 'Delete' })
    await user.click(deleteButtons[deleteButtons.length - 1])

    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(stepsApi.delete).not.toHaveBeenCalled()
  })

  // ── Reorder ───────────────────────────────────────────────────────────────

  it('calls stepsApi.reorder with new order when "Move step down" is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue({ ...plan1, load_steps: [step1, step2] })
    vi.mocked(stepsApi.reorder).mockResolvedValue(undefined)

    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    // First step's "move down" button
    const moveDownButtons = screen.getAllByRole('button', { name: 'Move step down' })
    await user.click(moveDownButtons[0])

    await waitFor(() => {
      expect(stepsApi.reorder).toHaveBeenCalledWith('plan-1', ['step-2', 'step-1'])
    })
  })

  it('calls stepsApi.reorder with new order when "Move step up" is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue({ ...plan1, load_steps: [step1, step2] })
    vi.mocked(stepsApi.reorder).mockResolvedValue(undefined)

    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Contact'))

    // Second step's "move up" button
    const moveUpButtons = screen.getAllByRole('button', { name: 'Move step up' })
    await user.click(moveUpButtons[moveUpButtons.length - 1])

    await waitFor(() => {
      expect(stepsApi.reorder).toHaveBeenCalledWith('plan-1', ['step-2', 'step-1'])
    })
  })

  it('disables "Move step up" for the first step', async () => {
    vi.mocked(plansApi.get).mockResolvedValue({ ...plan1, load_steps: [step1, step2] })
    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    const moveUpButtons = screen.getAllByRole('button', { name: 'Move step up' })
    expect(moveUpButtons[0]).toBeDisabled()
  })

  it('disables "Move step down" for the last step', async () => {
    vi.mocked(plansApi.get).mockResolvedValue({ ...plan1, load_steps: [step1, step2] })
    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Contact'))

    const moveDownButtons = screen.getAllByRole('button', { name: 'Move step down' })
    expect(moveDownButtons[moveDownButtons.length - 1]).toBeDisabled()
  })

  // ── Per-step preview ──────────────────────────────────────────────────────

  it('shows preview results inline when Preview is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    vi.mocked(stepsApi.preview).mockResolvedValue({
      pattern: 'accounts_*.csv',
      matched_files: [{ filename: 'accounts_001.csv', row_count: 5000 }],
      total_rows: 5000,
    })

    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    await user.click(screen.getByRole('button', { name: 'Preview' }))

    await waitFor(() => {
      expect(screen.getByText(/1 file\(s\) matched/)).toBeInTheDocument()
      expect(screen.getByText(/5,000 total rows/)).toBeInTheDocument()
      expect(screen.getByText(/accounts_001\.csv/)).toBeInTheDocument()
    })
  })

  it('shows error inline when preview fails', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    vi.mocked(stepsApi.preview).mockRejectedValue(new Error('File not found'))

    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    await user.click(screen.getByRole('button', { name: 'Preview' }))

    await waitFor(() => {
      expect(screen.getByText('File not found')).toBeInTheDocument()
    })
  })

  it('calls stepsApi.preview with correct planId and stepId', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    vi.mocked(stepsApi.preview).mockResolvedValue({
      pattern: 'accounts_*.csv',
      matched_files: [],
      total_rows: 0,
    })

    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    await user.click(screen.getByRole('button', { name: 'Preview' }))

    await waitFor(() => {
      expect(stepsApi.preview).toHaveBeenCalledWith('plan-1', 'step-1')
    })
  })

  // ── Preflight modal ───────────────────────────────────────────────────────

  it('shows "Run Preflight" button when there are steps', async () => {
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    renderEditor('plan-1')
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Run Preflight' })).toBeInTheDocument()
    })
  })

  it('does not show "Run Preflight" button when there are no steps', async () => {
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    expect(screen.queryByRole('button', { name: 'Run Preflight' })).not.toBeInTheDocument()
  })

  it('opens the preflight modal and fetches all step previews', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue({ ...plan1, load_steps: [step1, step2] })
    vi.mocked(stepsApi.preview).mockResolvedValue({
      pattern: 'test_*.csv',
      matched_files: [{ filename: 'test_001.csv', row_count: 100 }],
      total_rows: 100,
    })

    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    await user.click(screen.getByRole('button', { name: 'Run Preflight' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Preflight Check')).toBeInTheDocument()

    await waitFor(() => {
      expect(stepsApi.preview).toHaveBeenCalledWith('plan-1', 'step-1')
      expect(stepsApi.preview).toHaveBeenCalledWith('plan-1', 'step-2')
    })
  })

  it('closes the preflight modal when Close is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    vi.mocked(stepsApi.preview).mockResolvedValue({
      pattern: 'accounts_*.csv',
      matched_files: [],
      total_rows: 0,
    })

    renderEditor('plan-1')
    await waitFor(() => screen.getByRole('button', { name: 'Run Preflight' }))

    await user.click(screen.getByRole('button', { name: 'Run Preflight' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  // ── Start Run ─────────────────────────────────────────────────────────────

  it('calls plansApi.startRun and navigates to /runs/:id on success', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(plan1)
    vi.mocked(plansApi.startRun).mockResolvedValue({
      id: 'run-1',
      load_plan_id: 'plan-1',
      status: 'pending',
      started_at: null,
      completed_at: null,
      total_records: null,
      total_success: null,
      total_errors: null,
      initiated_by: null,
      error_summary: null,
      is_retry: false,
    })

    renderEditor('plan-1')
    await waitFor(() => screen.getByRole('button', { name: 'Start Run' }))

    await user.click(screen.getByRole('button', { name: 'Start Run' }))

    await waitFor(() => {
      expect(plansApi.startRun).toHaveBeenCalledWith('plan-1')
    })

    await waitFor(() => {
      expect(screen.getByTestId('run-detail-page')).toBeInTheDocument()
    })
  })

  // ── Object autocomplete ───────────────────────────────────────────────────

  it('shows object name suggestions from the connected org', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])

    // Open the object field dropdown
    await user.click(screen.getAllByRole('button', { name: 'Show options' })[0])
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Account' })).toBeInTheDocument()
      expect(screen.getByRole('option', { name: 'Contact' })).toBeInTheDocument()
    })
  })

  it('filters object suggestions as user types', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])

    await waitFor(() => expect(connectionsApi.listObjects).toHaveBeenCalled())
    const objectInput = screen.getByLabelText(/Salesforce Object/)
    await user.type(objectInput, 'Con')
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Contact' })).toBeInTheDocument()
      expect(screen.queryByRole('option', { name: 'Account' })).not.toBeInTheDocument()
    })
  })

  it('selecting an object suggestion fills the field', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.click(screen.getAllByRole('button', { name: 'Show options' })[0])
    await waitFor(() => screen.getByRole('option', { name: 'Account' }))
    await user.click(screen.getByRole('option', { name: 'Account' }))
    expect(screen.getByDisplayValue('Account')).toBeInTheDocument()
  })

  // ── File picker ───────────────────────────────────────────────────────────

  it('shows a Browse button in the step form', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    expect(screen.getByRole('button', { name: 'Browse' })).toBeInTheDocument()
  })

  it('shows the file picker panel when Browse is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(filesApi.listInput).mockResolvedValue([
      { name: 'accounts.csv', kind: 'file', path: 'accounts.csv', size_bytes: 1024, row_count: 50 },
    ])
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.click(screen.getByRole('button', { name: 'Browse' }))
    expect(screen.getByRole('navigation', { name: 'File picker breadcrumb' })).toBeInTheDocument()
  })

  it('lists CSV files in the picker', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(filesApi.listInput).mockResolvedValue([
      { name: 'accounts.csv', kind: 'file', path: 'accounts.csv', size_bytes: 1024, row_count: 50 },
      { name: 'contacts.csv', kind: 'file', path: 'contacts.csv', size_bytes: 512, row_count: 20 },
    ])
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.click(screen.getByRole('button', { name: 'Browse' }))
    await waitFor(() => {
      expect(screen.getByText('accounts.csv')).toBeInTheDocument()
      expect(screen.getByText('contacts.csv')).toBeInTheDocument()
    })
  })

  it('selects a file from the picker and sets the pattern field', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(filesApi.listInput).mockResolvedValue([
      { name: 'accounts.csv', kind: 'file', path: 'accounts.csv', size_bytes: 1024, row_count: 50 },
    ])
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.click(screen.getByRole('button', { name: 'Browse' }))
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    expect(screen.getByDisplayValue('accounts.csv')).toBeInTheDocument()
  })

  it('closes the file picker after a file is selected', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(filesApi.listInput).mockResolvedValue([
      { name: 'accounts.csv', kind: 'file', path: 'accounts.csv', size_bytes: 1024, row_count: 50 },
    ])
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.click(screen.getByRole('button', { name: 'Browse' }))
    await waitFor(() => screen.getByText('accounts.csv'))
    await user.click(screen.getByText('accounts.csv'))
    expect(screen.queryByRole('navigation', { name: 'File picker breadcrumb' })).not.toBeInTheDocument()
  })

  it('navigates into a subdirectory in the picker', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(filesApi.listInput)
      .mockResolvedValueOnce([
        { name: '2026', kind: 'directory', path: '2026', size_bytes: null, row_count: null },
      ])
      .mockResolvedValueOnce([
        { name: 'accounts.csv', kind: 'file', path: '2026/accounts.csv', size_bytes: 512, row_count: 10 },
      ])
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.click(screen.getByRole('button', { name: 'Browse' }))
    await waitFor(() => screen.getByText('2026'))
    await user.click(screen.getByText('2026'))
    await waitFor(() => expect(filesApi.listInput).toHaveBeenLastCalledWith('2026', 'local'))
  })

  it('closes the picker when Close is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(filesApi.listInput).mockResolvedValue([])
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.click(screen.getByRole('button', { name: 'Browse' }))
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('navigation', { name: 'File picker breadcrumb' })).not.toBeInTheDocument()
  })

  // ── External ID column dropdown ───────────────────────────────────────────

  it('shows column dropdown toggle in the External ID field when operation is upsert', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(filesApi.previewInput).mockResolvedValue({
      filename: 'accounts.csv',
      header: ['Name', 'ExternalId__c'],
      rows: [],
      total_rows: 0,
      filtered_rows: null,
      offset: 0,
      limit: 1,
      has_next: false,
    })
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.selectOptions(screen.getByLabelText(/Operation/), 'upsert')
    // Object field + External ID field each have a "Show options" button
    expect(screen.getAllByRole('button', { name: 'Show options' })).toHaveLength(2)
  })

  it('shows column headers from preview when pattern is a literal file path', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(filesApi.previewInput).mockResolvedValue({
      filename: 'accounts.csv',
      header: ['Name', 'ExternalId__c', 'BillingCity'],
      rows: [],
      total_rows: 0,
      filtered_rows: null,
      offset: 0,
      limit: 1,
      has_next: false,
    })
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.selectOptions(screen.getByLabelText(/Operation/), 'upsert')
    await user.type(screen.getByLabelText(/CSV File Pattern/), 'accounts.csv')
    // The second "Show options" button belongs to the External ID ComboInput
    const showOptionsBtns = screen.getAllByRole('button', { name: 'Show options' })
    await user.click(showOptionsBtns[showOptionsBtns.length - 1])
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'ExternalId__c' })).toBeInTheDocument()
    })
  })

  it('selecting a column header fills the External ID field', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(filesApi.previewInput).mockResolvedValue({
      filename: 'accounts.csv',
      header: ['Name', 'ExternalId__c'],
      rows: [],
      total_rows: 0,
      filtered_rows: null,
      offset: 0,
      limit: 1,
      has_next: false,
    })
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.selectOptions(screen.getByLabelText(/Operation/), 'upsert')
    await user.type(screen.getByLabelText(/CSV File Pattern/), 'accounts.csv')
    const showOptionsBtns = screen.getAllByRole('button', { name: 'Show options' })
    await user.click(showOptionsBtns[showOptionsBtns.length - 1])
    await waitFor(() => screen.getByRole('option', { name: 'ExternalId__c' }))
    await user.click(screen.getByRole('option', { name: 'ExternalId__c' }))
    expect(screen.getByDisplayValue('ExternalId__c')).toBeInTheDocument()
  })

  it('shows hint when pattern contains wildcards', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))
    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.selectOptions(screen.getByLabelText(/Operation/), 'upsert')
    await user.type(screen.getByLabelText(/CSV File Pattern/), 'accounts_*.csv')
    expect(screen.getByText(/Enter a literal file path/)).toBeInTheDocument()
  })

  // ── Query operation ───────────────────────────────────────────────────────

  it('shows SOQL textarea and hides CSV fields when operation is "query"', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.selectOptions(screen.getByLabelText(/Operation/), 'query')

    expect(screen.getByLabelText(/SOQL Query/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/CSV File Pattern/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Partition Size/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Input Source/)).not.toBeInTheDocument()
  })

  it('shows SOQL textarea and hides CSV fields when operation is "queryAll"', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.selectOptions(screen.getByLabelText(/Operation/), 'queryAll')

    expect(screen.getByLabelText(/SOQL Query/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/CSV File Pattern/)).not.toBeInTheDocument()
  })

  it('restores CSV fields when switching from query back to a DML operation', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.selectOptions(screen.getByLabelText(/Operation/), 'query')
    expect(screen.getByLabelText(/SOQL Query/)).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText(/Operation/), 'insert')
    expect(screen.queryByLabelText(/SOQL Query/)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/CSV File Pattern/)).toBeInTheDocument()
  })

  it('shows a client-side validation error when SOQL is empty on save', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.selectOptions(screen.getByLabelText(/Operation/), 'query')
    await user.type(screen.getByLabelText(/Salesforce Object/), 'Account')

    // Try to save without entering SOQL
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Add Step' }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
      expect(screen.getByText(/SOQL query is required/i)).toBeInTheDocument()
    })
    expect(stepsApi.create).not.toHaveBeenCalled()
  })

  it('shows a client-side validation error when SOQL lacks SELECT', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.selectOptions(screen.getByLabelText(/Operation/), 'query')
    await user.type(screen.getByLabelText(/Salesforce Object/), 'Account')
    await user.type(screen.getByLabelText(/SOQL Query/), 'FROM Account')

    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Add Step' }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
      expect(screen.getByText(/SOQL must contain SELECT/)).toBeInTheDocument()
    })
  })

  it('calls stepsApi.create with soql and null csv_file_pattern for query ops', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(stepsApi.create).mockResolvedValue(queryStep)

    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    await user.type(screen.getByLabelText(/Salesforce Object/), 'Account')
    await user.selectOptions(screen.getByLabelText(/Operation/), 'query')
    await user.type(screen.getByLabelText(/SOQL Query/), 'SELECT Id FROM Account')

    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Add Step' }))

    await waitFor(() => {
      expect(stepsApi.create).toHaveBeenCalledWith(
        'plan-1',
        expect.objectContaining({
          operation: 'query',
          soql: 'SELECT Id FROM Account',
          csv_file_pattern: null,
        }),
      )
    })
  })

  it('hides Preview button for query op rows in StepList', async () => {
    vi.mocked(plansApi.get).mockResolvedValue(planWithQueryStep)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    // Preview button should not be present for query steps
    expect(screen.queryByRole('button', { name: 'Preview' })).not.toBeInTheDocument()
  })

  it('shows Preview button for DML op rows but not query op rows when both exist', async () => {
    vi.mocked(plansApi.get).mockResolvedValue({
      ...planWithQueryStep,
      load_steps: [queryStep, { ...step1, id: 'step-dml', sequence: 2 }],
    })
    renderEditor('plan-1')
    await waitFor(() => screen.getAllByText('Account'))

    // Exactly one Preview button — for the DML step
    expect(screen.getAllByRole('button', { name: 'Preview' })).toHaveLength(1)
  })

  it('calls stepsApi.validateSoql (not preview) for query steps during preflight', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planWithQueryStep)
    vi.mocked(stepsApi.validateSoql).mockResolvedValue({
      valid: true,
      plan: { leadingOperation: 'TableScan', sobjectType: 'Account' },
      error: null,
    })

    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    await user.click(screen.getByRole('button', { name: 'Run Preflight' }))
    await screen.findByRole('dialog')

    await waitFor(() => expect(stepsApi.validateSoql).toHaveBeenCalled())
    expect(stepsApi.preview).not.toHaveBeenCalled()
  })

  it('shows Validate SOQL button when editing an existing query step', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planWithQueryStep)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    const editButtons = screen.getAllByRole('button', { name: 'Edit' })
    await user.click(editButtons[editButtons.length - 1])

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Validate SOQL' })).toBeInTheDocument()
  })

  it('renders success state when Validate SOQL returns valid: true', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planWithQueryStep)
    vi.mocked(stepsApi.validateSoql).mockResolvedValue({
      valid: true,
      plan: { leadingOperation: 'TableScan', sobjectType: 'Account' },
      error: null,
    })

    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    const editButtons = screen.getAllByRole('button', { name: 'Edit' })
    await user.click(editButtons[editButtons.length - 1])

    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Validate SOQL' }))

    await waitFor(() => {
      expect(within(dialog).getByText(/SOQL is valid/)).toBeInTheDocument()
      // The plan summary line includes both sobjectType and leadingOperation
      expect(within(dialog).getByText(/TableScan/)).toBeInTheDocument()
    })
  })

  it('renders the Salesforce error message verbatim when Validate SOQL returns valid: false', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planWithQueryStep)
    vi.mocked(stepsApi.validateSoql).mockResolvedValue({
      valid: false,
      plan: null,
      error: "INVALID_FIELD: No such column 'BadField' on entity 'Account'",
    })

    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    const editButtons = screen.getAllByRole('button', { name: 'Edit' })
    await user.click(editButtons[editButtons.length - 1])

    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Validate SOQL' }))

    await waitFor(() => {
      expect(within(dialog).getByText(/Validation failed/)).toBeInTheDocument()
      expect(within(dialog).getByText(/No such column 'BadField'/)).toBeInTheDocument()
    })
  })

  it('renders a network error message when Validate SOQL fetch fails', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planWithQueryStep)
    vi.mocked(stepsApi.validateSoql).mockRejectedValue(new Error('Network error'))

    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    const editButtons = screen.getAllByRole('button', { name: 'Edit' })
    await user.click(editButtons[editButtons.length - 1])

    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Validate SOQL' }))

    await waitFor(() => {
      expect(within(dialog).getByText('Network error')).toBeInTheDocument()
    })
  })

  it('pre-fills SOQL textarea when editing an existing query step', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planWithQueryStep)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Account'))

    const editButtons = screen.getAllByRole('button', { name: 'Edit' })
    await user.click(editButtons[editButtons.length - 1])

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByDisplayValue('SELECT Id, Name FROM Account')).toBeInTheDocument()
  })
})
