/**
 * SFBL-264 — PlanEditor UI: step naming + cross-step reference picker
 *
 * Tests for:
 * - Step name input (raw value, placeholder = computed label when name null)
 * - Three-way input-source radio
 * - Mode switching clears irrelevant fields in payload
 * - Mode 3 (from_step) dropdown lists only preceding query steps
 * - StepList upstream-link badge
 * - Reorder 422 surfaces toast + reverts optimistic state
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../../components/ui/Toast'
import PlanEditor from '../../pages/PlanEditor'
import StepList from '../../components/StepList'
import type { LoadStep } from '../../api/types'

// ─── Mock AuthContext ─────────────────────────────────────────────────────────

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
    user: { id: '1', email: 'admin@example.com', is_admin: true, profile: { name: 'admin' }, permissions: [...MOCK_ALL_PERMISSIONS] },
    permissions: MOCK_ALL_PERMISSIONS,
    profileName: 'admin',
    isBootstrapping: false,
    authRequired: true,
    login: vi.fn(),
    logout: vi.fn(),
  })),
  useAuthOptional: vi.fn(() => ({
    token: 'test-token',
    user: { id: '1', email: 'admin@example.com', is_admin: true, profile: { name: 'admin' }, permissions: [...MOCK_ALL_PERMISSIONS] },
    permissions: MOCK_ALL_PERMISSIONS,
    profileName: 'admin',
    isBootstrapping: false,
    authRequired: true,
    login: vi.fn(),
    logout: vi.fn(),
  })),
}))

// ─── Mock endpoints ───────────────────────────────────────────────────────────

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

// ─── Fixtures ─────────────────────────────────────────────────────────────────

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

const queryStep: LoadStep = {
  id: 'step-query',
  load_plan_id: 'plan-1',
  sequence: 1,
  object_name: 'Account',
  operation: 'query',
  name: null,
  csv_file_pattern: null,
  soql: 'SELECT Id FROM Account',
  partition_size: 10000,
  external_id_field: null,
  assignment_rule_id: null,
  input_connection_id: null,
  input_from_step_id: null,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
}

const namedQueryStep: LoadStep = {
  ...queryStep,
  id: 'step-named-query',
  name: 'account_ids',
}

const dmlStep: LoadStep = {
  id: 'step-dml',
  load_plan_id: 'plan-1',
  sequence: 2,
  object_name: 'Contact',
  operation: 'insert',
  name: null,
  csv_file_pattern: 'contacts_*.csv',
  soql: null,
  partition_size: 10000,
  external_id_field: null,
  assignment_rule_id: null,
  input_connection_id: null,
  input_from_step_id: null,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
}

const linkedDmlStep: LoadStep = {
  ...dmlStep,
  id: 'step-linked-dml',
  input_from_step_id: 'step-query',
  csv_file_pattern: null,
  input_connection_id: null,
}

const namedLinkedDmlStep: LoadStep = {
  ...linkedDmlStep,
  input_from_step_id: 'step-named-query',
}

const plan1 = {
  id: 'plan-1',
  connection_id: 'conn-1',
  name: 'Test Plan',
  description: null,
  abort_on_step_failure: true,
  error_threshold_pct: 10,
  max_parallel_jobs: 5,
  consecutive_failure_threshold: null,
  output_connection_id: null,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
  load_steps: [],
}

const planNoSteps = { ...plan1, load_steps: [] }
const planWithQueryStep = { ...plan1, load_steps: [queryStep] }
const planWithQueryAndDml = { ...plan1, load_steps: [queryStep, dmlStep] }
const planWithNamedQueryStep = { ...plan1, load_steps: [namedQueryStep] }

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
}

function renderEditor(planId: string) {
  const qc = makeQC()
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={[`/plans/${planId}`]}>
          <Routes>
            <Route path="/plans/:id" element={<PlanEditor />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(connectionsApi.list).mockResolvedValue([conn1])
  vi.mocked(connectionsApi.listObjects).mockResolvedValue([])
  vi.mocked(inputConnectionsApi.list).mockResolvedValue([])
  vi.mocked(filesApi.listInput).mockResolvedValue([])
  vi.mocked(filesApi.previewInput).mockResolvedValue({
    filename: 'accounts.csv',
    header: [],
    rows: [],
    total_rows: 0,
    filtered_rows: null,
    offset: 0,
    limit: 1,
    has_next: false,
  })
  vi.mocked(notificationSubscriptionsApi.list).mockResolvedValue([])
  vi.mocked(stepsApi.reorder).mockResolvedValue(undefined)
})

// ─── Step name tests ──────────────────────────────────────────────────────────

describe('Step name input (SFBL-264)', () => {
  it('renders the Step Name input when the modal opens', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText(/Step Name/)).toBeInTheDocument()
  })

  it('shows computed label as placeholder when name is null (new step)', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    const dialog = screen.getByRole('dialog')

    // Type an object name and operation so placeholder is non-trivial
    await user.type(within(dialog).getByLabelText(/Salesforce Object/), 'Account')
    await user.selectOptions(within(dialog).getByLabelText(/Operation/), 'insert')

    const nameInput = within(dialog).getByLabelText(/Step Name/)
    // Value is empty (not pre-filled) — placeholder shows the computed label
    expect(nameInput).toHaveValue('')
    expect(nameInput).toHaveAttribute('placeholder')
    const placeholder = nameInput.getAttribute('placeholder') ?? ''
    expect(placeholder).toMatch(/insert/i)
    expect(placeholder).toMatch(/Account/)
  })

  it('shows the persisted name as value when editing a named step', async () => {
    const user = userEvent.setup()
    const namedStep: LoadStep = { ...queryStep, name: 'my_query_step', operation: 'query' }
    vi.mocked(plansApi.get).mockResolvedValue({ ...plan1, load_steps: [namedStep] })
    renderEditor('plan-1')
    // The step renders with name "my_query_step" (not "Account") in the list
    await waitFor(() => screen.getByText('my_query_step'))

    const editButtons = screen.getAllByRole('button', { name: 'Edit' })
    await user.click(editButtons[editButtons.length - 1])

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText(/Step Name/)).toHaveValue('my_query_step')
  })

  it('sends raw name value (including empty string) on save — no normalisation', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(stepsApi.create).mockResolvedValue(queryStep)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    const dialog = screen.getByRole('dialog')

    await user.type(within(dialog).getByLabelText(/Salesforce Object/), 'Account')
    await user.selectOptions(within(dialog).getByLabelText(/Operation/), 'query')
    await user.type(within(dialog).getByLabelText(/SOQL Query/), 'SELECT Id FROM Account')
    // Type a name, then clear it — should send "" not null
    await user.type(within(dialog).getByLabelText(/Step Name/), 'temp')
    await user.clear(within(dialog).getByLabelText(/Step Name/))

    await user.click(within(dialog).getByRole('button', { name: 'Add Step' }))

    await waitFor(() => {
      expect(stepsApi.create).toHaveBeenCalledWith(
        'plan-1',
        expect.objectContaining({ name: '' }),
      )
    })
  })

  it('sends the typed name raw on save', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(stepsApi.create).mockResolvedValue(queryStep)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    const dialog = screen.getByRole('dialog')

    await user.type(within(dialog).getByLabelText(/Salesforce Object/), 'Account')
    await user.selectOptions(within(dialog).getByLabelText(/Operation/), 'query')
    await user.type(within(dialog).getByLabelText(/SOQL Query/), 'SELECT Id FROM Account')
    await user.type(within(dialog).getByLabelText(/Step Name/), '  My Step  ')

    await user.click(within(dialog).getByRole('button', { name: 'Add Step' }))

    await waitFor(() => {
      expect(stepsApi.create).toHaveBeenCalledWith(
        'plan-1',
        expect.objectContaining({ name: '  My Step  ' }),
      )
    })
  })
})

// ─── Three-way input source radio tests ──────────────────────────────────────

describe('Three-way input source radio (SFBL-264)', () => {
  it('renders all three radio options for DML steps', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    const dialog = screen.getByRole('dialog')

    expect(within(dialog).getByRole('radio', { name: 'Input connection / CSV file' })).toBeInTheDocument()
    expect(within(dialog).getByRole('radio', { name: 'Local output (prior run results)' })).toBeInTheDocument()
    expect(within(dialog).getByRole('radio', { name: 'From upstream step in this run' })).toBeInTheDocument()
  })

  it('defaults to pattern mode (Input connection / CSV file)', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    const dialog = screen.getByRole('dialog')

    expect(within(dialog).getByRole('radio', { name: 'Input connection / CSV file' })).toBeChecked()
    expect(within(dialog).getByRole('radio', { name: 'Local output (prior run results)' })).not.toBeChecked()
    expect(within(dialog).getByRole('radio', { name: 'From upstream step in this run' })).not.toBeChecked()
  })

  it('switching to local_output mode sends input_connection_id=local-output in payload', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    vi.mocked(stepsApi.create).mockResolvedValue(dmlStep)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    const dialog = screen.getByRole('dialog')

    await user.type(within(dialog).getByLabelText(/Salesforce Object/), 'Contact')
    await user.click(within(dialog).getByRole('radio', { name: 'Local output (prior run results)' }))
    await user.type(within(dialog).getByLabelText(/CSV File Pattern/), 'contacts_*.csv')

    await user.click(within(dialog).getByRole('button', { name: 'Add Step' }))

    await waitFor(() => {
      expect(stepsApi.create).toHaveBeenCalledWith(
        'plan-1',
        expect.objectContaining({
          input_connection_id: 'local-output',
          input_from_step_id: null,
          csv_file_pattern: 'contacts_*.csv',
        }),
      )
    })
  })

  it('switching to from_step mode sends input_from_step_id and nulls csv/connection', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planWithQueryStep)
    vi.mocked(stepsApi.create).mockResolvedValue(linkedDmlStep)
    renderEditor('plan-1')
    // Wait for the step list to show, then click the Add Step button
    const addStepBtn = await screen.findByRole('button', { name: 'Add Step' })
    await user.click(addStepBtn)
    const dialog = screen.getByRole('dialog')

    await user.type(within(dialog).getByLabelText(/Salesforce Object/), 'Contact')
    await user.click(within(dialog).getByRole('radio', { name: 'From upstream step in this run' }))

    // Select the upstream query step
    await user.selectOptions(within(dialog).getByLabelText(/Upstream Query Step/), 'step-query')

    await user.click(within(dialog).getByRole('button', { name: 'Add Step' }))

    await waitFor(() => {
      expect(stepsApi.create).toHaveBeenCalledWith(
        'plan-1',
        expect.objectContaining({
          input_from_step_id: 'step-query',
          input_connection_id: null,
          csv_file_pattern: null,
        }),
      )
    })
  })

  it('switching back to pattern mode sends null input_from_step_id', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planWithQueryStep)
    vi.mocked(stepsApi.create).mockResolvedValue(dmlStep)
    renderEditor('plan-1')
    const addStepBtn = await screen.findByRole('button', { name: 'Add Step' })
    await user.click(addStepBtn)
    const dialog = screen.getByRole('dialog')

    await user.type(within(dialog).getByLabelText(/Salesforce Object/), 'Contact')
    // Switch to from_step then back to pattern
    await user.click(within(dialog).getByRole('radio', { name: 'From upstream step in this run' }))
    await user.click(within(dialog).getByRole('radio', { name: 'Input connection / CSV file' }))
    await user.type(within(dialog).getByLabelText(/CSV File Pattern/), 'contacts_*.csv')

    await user.click(within(dialog).getByRole('button', { name: 'Add Step' }))

    await waitFor(() => {
      expect(stepsApi.create).toHaveBeenCalledWith(
        'plan-1',
        expect.objectContaining({
          input_from_step_id: null,
          csv_file_pattern: 'contacts_*.csv',
        }),
      )
    })
  })

  it('hides CSV file pattern input in from_step mode', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planWithQueryStep)
    renderEditor('plan-1')
    const addStepBtn = await screen.findByRole('button', { name: 'Add Step' })
    await user.click(addStepBtn)
    const dialog = screen.getByRole('dialog')

    await user.click(within(dialog).getByRole('radio', { name: 'From upstream step in this run' }))

    expect(within(dialog).queryByLabelText(/CSV File Pattern/)).not.toBeInTheDocument()
  })

  it('preloads from_step mode when editing a step with input_from_step_id set', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue({ ...plan1, load_steps: [queryStep, linkedDmlStep] })
    renderEditor('plan-1')
    await waitFor(() => screen.getAllByText('Account'))

    // Edit the DML step (second step)
    const editButtons = screen.getAllByRole('button', { name: 'Edit' })
    await user.click(editButtons[1])

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('radio', { name: 'From upstream step in this run' })).toBeChecked()
    expect(within(dialog).getByLabelText(/Upstream Query Step/)).toHaveValue('step-query')
  })

  it('preloads local_output mode when editing a step with input_connection_id=local-output', async () => {
    const user = userEvent.setup()
    const localOutputStep: LoadStep = { ...dmlStep, input_connection_id: 'local-output' }
    vi.mocked(plansApi.get).mockResolvedValue({ ...plan1, load_steps: [localOutputStep] })
    renderEditor('plan-1')
    await waitFor(() => screen.getByText('Contact'))

    const editButtons = screen.getAllByRole('button', { name: 'Edit' })
    await user.click(editButtons[editButtons.length - 1])

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('radio', { name: 'Local output (prior run results)' })).toBeChecked()
  })
})

// ─── Upstream query step dropdown tests ──────────────────────────────────────

describe('Upstream query step dropdown (SFBL-264)', () => {
  it('shows empty-state message when no preceding query steps exist', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planNoSteps)
    renderEditor('plan-1')
    await waitFor(() => screen.getByText(/No steps yet/))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('radio', { name: 'From upstream step in this run' }))

    expect(within(dialog).getByTestId('no-upstream-steps')).toBeInTheDocument()
    expect(within(dialog).getByText(/Add a query step before this one/)).toBeInTheDocument()
  })

  it('lists only preceding query steps (not DML steps) in the dropdown', async () => {
    const user = userEvent.setup()
    // Plan has query step (seq 1) + DML step (seq 2); editing a new step (seq 3)
    vi.mocked(plansApi.get).mockResolvedValue(planWithQueryAndDml)
    renderEditor('plan-1')
    await waitFor(() => screen.getAllByText('Account'))

    await user.click(screen.getAllByRole('button', { name: 'Add Step' })[0])
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('radio', { name: 'From upstream step in this run' }))

    // Query step should appear in dropdown
    expect(within(dialog).getByRole('option', { name: /Step 1: query Account/ })).toBeInTheDocument()
    // DML (insert) step should NOT appear
    expect(within(dialog).queryByRole('option', { name: /Step 2: insert Contact/ })).not.toBeInTheDocument()
  })

  it('uses the step name in the dropdown option when name is set', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue(planWithNamedQueryStep)
    renderEditor('plan-1')
    // namedQueryStep renders with name "account_ids" in the list
    await waitFor(() => screen.getByText('account_ids'))

    const addStepBtn = screen.getByRole('button', { name: 'Add Step' })
    await user.click(addStepBtn)
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('radio', { name: 'From upstream step in this run' }))

    // Should show the named query step's name
    expect(within(dialog).getByRole('option', { name: 'account_ids' })).toBeInTheDocument()
  })

  it('does not list query steps that come after the current step', async () => {
    const user = userEvent.setup()
    // A query step at seq 2, a DML step at seq 1 — editing the DML should show no upstream steps
    const lateQueryStep: LoadStep = { ...queryStep, sequence: 2 }
    const earlyDmlStep: LoadStep = { ...dmlStep, sequence: 1 }
    vi.mocked(plansApi.get).mockResolvedValue({
      ...plan1,
      load_steps: [earlyDmlStep, lateQueryStep],
    })
    renderEditor('plan-1')
    await waitFor(() => screen.getAllByText('Contact'))

    const editButtons = screen.getAllByRole('button', { name: 'Edit' })
    // Edit the first step (DML, seq 1)
    await user.click(editButtons[0])
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('radio', { name: 'From upstream step in this run' }))

    // The query step is at seq 2 (after the DML at seq 1) so it should NOT appear
    expect(within(dialog).getByTestId('no-upstream-steps')).toBeInTheDocument()
  })
})

// ─── StepList upstream badge tests ───────────────────────────────────────────

describe('StepList upstream link badge (SFBL-264)', () => {
  function renderStepList(steps: LoadStep[]) {
    return render(
      <StepList
        steps={steps}
        previews={{}}
        inputConnections={[]}
        reorderPending={false}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onMoveUp={vi.fn()}
        onMoveDown={vi.fn()}
        onPreview={vi.fn()}
        onAddStep={vi.fn()}
      />,
    )
  }

  it('shows upstream badge on a step with input_from_step_id', () => {
    renderStepList([queryStep, linkedDmlStep])

    const badge = screen.getByTestId('upstream-badge')
    expect(badge).toBeInTheDocument()
    expect(badge.textContent).toContain('from')
    // Upstream step name is computed label (name=null → "Step 1: query Account")
    expect(badge.textContent).toContain('Step 1: query Account')
  })

  it('uses the upstream step name when it is set', () => {
    renderStepList([namedQueryStep, namedLinkedDmlStep])

    const badge = screen.getByTestId('upstream-badge')
    expect(badge.textContent).toContain('account_ids')
  })

  it('does not render an upstream badge on steps without input_from_step_id', () => {
    renderStepList([queryStep, dmlStep])

    expect(screen.queryByTestId('upstream-badge')).not.toBeInTheDocument()
  })
})

// ─── Reorder 422 handling ─────────────────────────────────────────────────────

describe('Reorder 422 handling (SFBL-264)', () => {
  it('surfaces the 422 error message in a toast when reorder is rejected', async () => {
    const user = userEvent.setup()
    vi.mocked(plansApi.get).mockResolvedValue({
      ...plan1,
      load_steps: [queryStep, linkedDmlStep],
    })
    vi.mocked(stepsApi.reorder).mockRejectedValue(
      new Error('Reorder would invert reference: Contact (seq 2) → Account (seq 1)'),
    )

    renderEditor('plan-1')
    await waitFor(() => screen.getAllByText('Account'))

    // Click move-down on the query step (which would invert the reference)
    const moveDownButtons = screen.getAllByRole('button', { name: 'Move step down' })
    await user.click(moveDownButtons[0])

    await waitFor(() => {
      expect(stepsApi.reorder).toHaveBeenCalled()
      expect(
        screen.getByText(/Reorder would invert reference/),
      ).toBeInTheDocument()
    })
  })
})
