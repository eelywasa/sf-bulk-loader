import { useState, useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faFolder, faChevronRight, faFile } from '@fortawesome/free-solid-svg-icons'
import { Button, Card, Modal, Badge, ComboInput } from '../components/ui'
import { useToast } from '../components/ui/Toast'
import {
  plansApi,
  stepsApi,
  connectionsApi,
  filesApi,
  type LoadPlanCreate,
  type LoadStepCreate,
} from '../api/endpoints'
import { ApiError } from '../api/client'
import type { LoadStep, StepPreviewResponse, ApiValidationError, InputDirectoryEntry } from '../api/types'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function extractErrors(err: unknown): string[] {
  if (err instanceof ApiError) {
    if (Array.isArray(err.detail)) {
      return (err.detail as ApiValidationError[]).map(
        (e) => `${e.loc.slice(1).join('.')} — ${e.msg}`,
      )
    }
    if (err.message) return [err.message]
  }
  if (err instanceof Error) return [err.message]
  return ['An unexpected error occurred']
}

const OPERATIONS = [
  { value: 'insert', label: 'Insert' },
  { value: 'update', label: 'Update' },
  { value: 'upsert', label: 'Upsert' },
  { value: 'delete', label: 'Delete' },
] as const

function operationVariant(op: string) {
  switch (op) {
    case 'insert':
      return 'info' as const
    case 'update':
      return 'warning' as const
    case 'upsert':
      return 'success' as const
    case 'delete':
      return 'error' as const
    default:
      return 'neutral' as const
  }
}

const INPUT_CLASS =
  'w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500'
const LABEL_CLASS = 'block text-sm font-medium text-gray-700 mb-1'

// ─── Form types ───────────────────────────────────────────────────────────────

interface PlanFormData {
  name: string
  description: string
  connection_id: string
  abort_on_step_failure: boolean
  error_threshold_pct: string
  max_parallel_jobs: string
}

const EMPTY_PLAN_FORM: PlanFormData = {
  name: '',
  description: '',
  connection_id: '',
  abort_on_step_failure: true,
  error_threshold_pct: '10',
  max_parallel_jobs: '5',
}

interface StepFormData {
  object_name: string
  operation: string
  csv_file_pattern: string
  partition_size: string
  external_id_field: string
  assignment_rule_id: string
}

const EMPTY_STEP_FORM: StepFormData = {
  object_name: '',
  operation: 'insert',
  csv_file_pattern: '',
  partition_size: '10000',
  external_id_field: '',
  assignment_rule_id: '',
}

// ─── Preview state ────────────────────────────────────────────────────────────

type PreviewEntry =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: StepPreviewResponse }
  | { status: 'error'; message: string }

// ─── File picker ──────────────────────────────────────────────────────────────

interface FilePickerProps {
  onSelect: (path: string) => void
  onClose: () => void
}

function FilePicker({ onSelect, onClose }: FilePickerProps) {
  const [currentPath, setCurrentPath] = useState('')
  const segments = currentPath ? currentPath.split('/').filter(Boolean) : []

  const { data: entries = [], isLoading } = useQuery({
    queryKey: ['files', 'input', currentPath],
    queryFn: () => filesApi.listInput(currentPath),
  })

  function navigate(path: string) {
    setCurrentPath(path)
  }

  return (
    <div className="mt-2 rounded-md border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 overflow-hidden">
      {/* Breadcrumb */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <nav aria-label="File picker breadcrumb" className="flex items-center gap-1 text-xs flex-wrap min-w-0">
          <button
            type="button"
            onClick={() => navigate('')}
            className={`transition-colors ${segments.length === 0 ? 'font-semibold text-gray-900 dark:text-gray-100' : 'text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300'}`}
          >
            Input Files
          </button>
          {segments.map((seg, i) => {
            const segPath = segments.slice(0, i + 1).join('/')
            const isLast = i === segments.length - 1
            return (
              <span key={segPath} className="flex items-center gap-1">
                <FontAwesomeIcon icon={faChevronRight} className="text-gray-400 dark:text-gray-500 text-[10px]" />
                {isLast ? (
                  <span className="font-semibold text-gray-900 dark:text-gray-100 truncate max-w-[8rem]">{seg}</span>
                ) : (
                  <button
                    type="button"
                    onClick={() => navigate(segPath)}
                    className="text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 transition-colors truncate max-w-[8rem]"
                  >
                    {seg}
                  </button>
                )}
              </span>
            )
          })}
        </nav>
        <button
          type="button"
          onClick={onClose}
          className="ml-2 shrink-0 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
        >
          Close
        </button>
      </div>

      {/* Entry list */}
      <ul className="max-h-48 overflow-y-auto divide-y divide-gray-100 dark:divide-gray-700">
        {isLoading && (
          <li className="px-3 py-3 text-xs text-gray-400 dark:text-gray-500 italic">Loading…</li>
        )}
        {!isLoading && entries.length === 0 && (
          <li className="px-3 py-3 text-xs text-gray-400 dark:text-gray-500 italic">No CSV files found here.</li>
        )}
        {entries.map((entry: InputDirectoryEntry) => (
          <li key={entry.path}>
            <button
              type="button"
              onClick={() => entry.kind === 'directory' ? navigate(entry.path) : onSelect(entry.path)}
              className="w-full text-left px-3 py-2 flex items-center gap-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            >
              <FontAwesomeIcon
                icon={entry.kind === 'directory' ? faFolder : faFile}
                className={entry.kind === 'directory' ? 'text-amber-400 shrink-0' : 'text-gray-400 dark:text-gray-500 shrink-0'}
                aria-hidden="true"
              />
              <span className="truncate text-gray-900 dark:text-gray-100">{entry.name}</span>
              {entry.kind === 'file' && entry.row_count != null && (
                <span className="ml-auto shrink-0 text-xs text-gray-400 dark:text-gray-500">
                  {entry.row_count.toLocaleString()} rows
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function PlanEditor() {
  const { id } = useParams<{ id: string }>()
  const isNew = id === 'new'
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const toast = useToast()

  // ── Plan form state ─────────────────────────────────────────────────────────

  const [form, setForm] = useState<PlanFormData>(EMPTY_PLAN_FORM)
  const [formErrors, setFormErrors] = useState<string[]>([])

  // ── Step modal state ────────────────────────────────────────────────────────

  const [stepModalOpen, setStepModalOpen] = useState(false)
  const [editingStep, setEditingStep] = useState<LoadStep | null>(null)
  const [stepForm, setStepForm] = useState<StepFormData>(EMPTY_STEP_FORM)
  const [stepFormErrors, setStepFormErrors] = useState<string[]>([])
  const [deleteStepTarget, setDeleteStepTarget] = useState<LoadStep | null>(null)

  // ── File picker + column header state ──────────────────────────────────────

  const [showFilePicker, setShowFilePicker] = useState(false)

  const patternIsLiteral =
    stepModalOpen &&
    stepForm.operation === 'upsert' &&
    stepForm.csv_file_pattern.length > 0 &&
    !stepForm.csv_file_pattern.includes('*') &&
    !stepForm.csv_file_pattern.includes('?')

  const { data: patternPreview, isLoading: patternPreviewLoading } = useQuery({
    queryKey: ['files', 'preview-headers', stepForm.csv_file_pattern],
    queryFn: () => filesApi.previewInput(stepForm.csv_file_pattern, 1),
    enabled: patternIsLiteral,
  })

  const columnHeaders: string[] = patternPreview?.header ?? []

  // ── Preview state ───────────────────────────────────────────────────────────

  const [previews, setPreviews] = useState<Record<string, PreviewEntry>>({})
  const [preflightOpen, setPreflightOpen] = useState(false)

  // ── Queries ─────────────────────────────────────────────────────────────────

  const {
    data: plan,
    isLoading: planLoading,
    error: planError,
  } = useQuery({
    queryKey: ['plans', id],
    queryFn: () => plansApi.get(id!),
    enabled: !isNew,
  })

  const { data: connections } = useQuery({
    queryKey: ['connections'],
    queryFn: connectionsApi.list,
  })

  const connectionId = form.connection_id || plan?.connection_id || ''
  const { data: sfObjects = [], isLoading: sfObjectsLoading } = useQuery({
    queryKey: ['connections', connectionId, 'objects'],
    queryFn: () => connectionsApi.listObjects(connectionId),
    enabled: stepModalOpen && connectionId !== '',
    staleTime: 5 * 60 * 1000, // cache for 5 minutes — object list rarely changes
  })

  // Sync form with loaded plan data
  useEffect(() => {
    if (plan) {
      setForm({
        name: plan.name,
        description: plan.description ?? '',
        connection_id: plan.connection_id,
        abort_on_step_failure: plan.abort_on_step_failure,
        error_threshold_pct: String(plan.error_threshold_pct),
        max_parallel_jobs: String(plan.max_parallel_jobs),
      })
    }
  }, [plan])

  // ── Mutations ───────────────────────────────────────────────────────────────

  const createPlanMutation = useMutation({
    mutationFn: (data: LoadPlanCreate) => plansApi.create(data),
    onSuccess: (newPlan) => {
      queryClient.invalidateQueries({ queryKey: ['plans'] })
      toast.success('Plan created')
      navigate(`/plans/${newPlan.id}`)
    },
    onError: (err) => setFormErrors(extractErrors(err)),
  })

  const updatePlanMutation = useMutation({
    mutationFn: (data: Partial<LoadPlanCreate>) => plansApi.update(id!, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['plans', id] })
      queryClient.invalidateQueries({ queryKey: ['plans'] })
      toast.success('Plan saved')
    },
    onError: (err) => setFormErrors(extractErrors(err)),
  })

  const createStepMutation = useMutation({
    mutationFn: (data: LoadStepCreate) => stepsApi.create(id!, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['plans', id] })
      toast.success('Step added')
      closeStepModal()
    },
    onError: (err) => setStepFormErrors(extractErrors(err)),
  })

  const updateStepMutation = useMutation({
    mutationFn: ({ stepId, data }: { stepId: string; data: Partial<LoadStepCreate> }) =>
      stepsApi.update(id!, stepId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['plans', id] })
      toast.success('Step updated')
      closeStepModal()
    },
    onError: (err) => setStepFormErrors(extractErrors(err)),
  })

  const deleteStepMutation = useMutation({
    mutationFn: (stepId: string) => stepsApi.delete(id!, stepId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['plans', id] })
      toast.success('Step deleted')
      setDeleteStepTarget(null)
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : 'Failed to delete step')
      setDeleteStepTarget(null)
    },
  })

  const reorderMutation = useMutation({
    mutationFn: (stepIds: string[]) => stepsApi.reorder(id!, stepIds),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['plans', id] })
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : 'Failed to reorder steps')
    },
  })

  const startRunMutation = useMutation({
    mutationFn: () => plansApi.startRun(id!),
    onSuccess: (run) => {
      toast.success('Run started')
      navigate(`/runs/${run.id}`)
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : 'Failed to start run')
    },
  })

  // ── Handlers ─────────────────────────────────────────────────────────────────

  function setPlanField<K extends keyof PlanFormData>(key: K, value: PlanFormData[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  function handleSavePlan() {
    setFormErrors([])
    const data: LoadPlanCreate = {
      name: form.name,
      connection_id: form.connection_id,
      description: form.description || null,
      abort_on_step_failure: form.abort_on_step_failure,
      error_threshold_pct: Number(form.error_threshold_pct),
      max_parallel_jobs: Number(form.max_parallel_jobs),
    }
    if (isNew) {
      createPlanMutation.mutate(data)
    } else {
      updatePlanMutation.mutate(data)
    }
  }

  function openCreateStep() {
    setEditingStep(null)
    setStepForm(EMPTY_STEP_FORM)
    setStepFormErrors([])
    setShowFilePicker(false)
    setStepModalOpen(true)
  }

  function openEditStep(step: LoadStep) {
    setEditingStep(step)
    setStepForm({
      object_name: step.object_name,
      operation: step.operation,
      csv_file_pattern: step.csv_file_pattern,
      partition_size: String(step.partition_size),
      external_id_field: step.external_id_field ?? '',
      assignment_rule_id: step.assignment_rule_id ?? '',
    })
    setStepFormErrors([])
    setShowFilePicker(false)
    setStepModalOpen(true)
  }

  function closeStepModal() {
    setStepModalOpen(false)
    setEditingStep(null)
    setStepForm(EMPTY_STEP_FORM)
    setStepFormErrors([])
    setShowFilePicker(false)
  }

  function setStepField<K extends keyof StepFormData>(key: K, value: StepFormData[K]) {
    setStepForm((prev) => ({ ...prev, [key]: value }))
  }

  function handleSaveStep() {
    setStepFormErrors([])
    const data: LoadStepCreate = {
      object_name: stepForm.object_name,
      operation: stepForm.operation,
      csv_file_pattern: stepForm.csv_file_pattern,
      partition_size: Number(stepForm.partition_size),
      external_id_field: stepForm.external_id_field || null,
      assignment_rule_id: stepForm.assignment_rule_id || null,
    }
    if (editingStep) {
      updateStepMutation.mutate({ stepId: editingStep.id, data })
    } else {
      createStepMutation.mutate(data)
    }
  }

  const sortedSteps = [...(plan?.load_steps ?? [])].sort((a, b) => a.sequence - b.sequence)

  function handleMoveUp(step: LoadStep) {
    const idx = sortedSteps.findIndex((s) => s.id === step.id)
    if (idx <= 0) return
    const newOrder = [...sortedSteps]
    ;[newOrder[idx - 1], newOrder[idx]] = [newOrder[idx], newOrder[idx - 1]]
    reorderMutation.mutate(newOrder.map((s) => s.id))
  }

  function handleMoveDown(step: LoadStep) {
    const idx = sortedSteps.findIndex((s) => s.id === step.id)
    if (idx < 0 || idx >= sortedSteps.length - 1) return
    const newOrder = [...sortedSteps]
    ;[newOrder[idx], newOrder[idx + 1]] = [newOrder[idx + 1], newOrder[idx]]
    reorderMutation.mutate(newOrder.map((s) => s.id))
  }

  async function handlePreviewStep(step: LoadStep) {
    setPreviews((prev) => ({ ...prev, [step.id]: { status: 'loading' } }))
    try {
      const data = await stepsApi.preview(id!, step.id)
      setPreviews((prev) => ({ ...prev, [step.id]: { status: 'success', data } }))
    } catch (err) {
      setPreviews((prev) => ({
        ...prev,
        [step.id]: {
          status: 'error',
          message: err instanceof Error ? err.message : 'Preview failed',
        },
      }))
    }
  }

  async function handlePreflight() {
    setPreflightOpen(true)
    // Set all steps to loading first
    setPreviews((prev) => {
      const next = { ...prev }
      for (const step of sortedSteps) {
        next[step.id] = { status: 'loading' }
      }
      return next
    })
    // Fetch all previews in parallel
    await Promise.all(
      sortedSteps.map(async (step) => {
        try {
          const data = await stepsApi.preview(id!, step.id)
          setPreviews((prev) => ({ ...prev, [step.id]: { status: 'success', data } }))
        } catch (err) {
          setPreviews((prev) => ({
            ...prev,
            [step.id]: {
              status: 'error',
              message: err instanceof Error ? err.message : 'Preview failed',
            },
          }))
        }
      }),
    )
  }

  const isSavingPlan = createPlanMutation.isPending || updatePlanMutation.isPending
  const isSavingStep = createStepMutation.isPending || updateStepMutation.isPending

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-6">
      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <nav className="flex items-center gap-2 text-sm text-gray-500 mb-1">
            <Link to="/plans" className="hover:text-gray-900">
              Load Plans
            </Link>
            <span>›</span>
            <span className="text-gray-900">
              {isNew ? 'New Plan' : (plan?.name ?? `Plan ${id}`)}
            </span>
          </nav>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
            {isNew ? 'New Load Plan' : 'Edit Load Plan'}
          </h1>
        </div>

        <div className="flex gap-2 shrink-0">
          {!isNew && sortedSteps.length > 0 && (
            <Button variant="secondary" onClick={() => void handlePreflight()}>
              Run Preflight
            </Button>
          )}
          {!isNew && (
            <Button
              variant="secondary"
              loading={startRunMutation.isPending}
              onClick={() => startRunMutation.mutate()}
            >
              Start Run
            </Button>
          )}
          <Button loading={isSavingPlan} onClick={handleSavePlan}>
            {isNew ? 'Save Plan' : 'Save Changes'}
          </Button>
        </div>
      </div>

      {/* ── Loading state (edit mode only) ──────────────────────────────────── */}
      {!isNew && planLoading && (
        <div className="flex justify-center py-16">
          <span
            aria-label="Loading"
            className="h-7 w-7 rounded-full border-2 border-blue-600 border-t-transparent animate-spin"
          />
        </div>
      )}

      {/* ── Error state (edit mode only) ────────────────────────────────────── */}
      {!isNew && planError && (
        <div className="rounded border border-red-200 bg-red-50 p-4">
          <p className="text-red-700 text-sm">
            Failed to load plan:{' '}
            {planError instanceof Error ? planError.message : 'Unknown error'}
          </p>
          <div className="mt-3">
            <Button variant="secondary" onClick={() => navigate('/plans')}>
              Back to Plans
            </Button>
          </div>
        </div>
      )}

      {/* ── Content (hidden while loading/errored in edit mode) ─────────────── */}
      {(isNew || (!planLoading && !planError)) && (
        <>
      {/* ── Plan Details ────────────────────────────────────────────────────── */}
      <Card title="Plan Details">
        <div className="space-y-4">
          {formErrors.length > 0 && (
            <div role="alert" className="rounded border border-red-200 bg-red-50 p-3 space-y-1">
              {formErrors.map((msg, i) => (
                <p key={i} className="text-sm text-red-700">
                  {msg}
                </p>
              ))}
            </div>
          )}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {/* Name */}
            <div className="sm:col-span-2">
              <label htmlFor="plan-name" className={LABEL_CLASS}>
                Name <span className="text-red-500">*</span>
              </label>
              <input
                id="plan-name"
                type="text"
                required
                value={form.name}
                onChange={(e) => setPlanField('name', e.target.value)}
                placeholder="Q1 Data Migration"
                className={INPUT_CLASS}
              />
            </div>

            {/* Description */}
            <div className="sm:col-span-2">
              <label htmlFor="plan-desc" className={LABEL_CLASS}>
                Description
              </label>
              <textarea
                id="plan-desc"
                rows={2}
                value={form.description}
                onChange={(e) => setPlanField('description', e.target.value)}
                placeholder="Optional description"
                className={clsx(INPUT_CLASS, 'resize-y')}
              />
            </div>

            {/* Connection */}
            <div className="sm:col-span-2">
              <label htmlFor="plan-connection" className={LABEL_CLASS}>
                Connection <span className="text-red-500">*</span>
              </label>
              <select
                id="plan-connection"
                value={form.connection_id}
                onChange={(e) => setPlanField('connection_id', e.target.value)}
                className={INPUT_CLASS}
              >
                <option value="">Select a connection…</option>
                {connections?.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Error threshold */}
            <div>
              <label htmlFor="plan-threshold" className={LABEL_CLASS}>
                Error Threshold (%)
              </label>
              <input
                id="plan-threshold"
                type="number"
                min="0"
                max="100"
                value={form.error_threshold_pct}
                onChange={(e) => setPlanField('error_threshold_pct', e.target.value)}
                className={INPUT_CLASS}
              />
            </div>

            {/* Max parallel jobs */}
            <div>
              <label htmlFor="plan-parallel" className={LABEL_CLASS}>
                Max Parallel Jobs
              </label>
              <input
                id="plan-parallel"
                type="number"
                min="1"
                max="25"
                value={form.max_parallel_jobs}
                onChange={(e) => setPlanField('max_parallel_jobs', e.target.value)}
                className={INPUT_CLASS}
              />
            </div>

            {/* Abort on step failure */}
            <div className="sm:col-span-2 flex items-center gap-3">
              <input
                id="plan-abort"
                type="checkbox"
                checked={form.abort_on_step_failure}
                onChange={(e) => setPlanField('abort_on_step_failure', e.target.checked)}
                className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
              <label htmlFor="plan-abort" className="text-sm font-medium text-gray-700">
                Abort run if a step fails
              </label>
            </div>
          </div>
        </div>
      </Card>

      {/* ── New plan: save-first hint ────────────────────────────────────────── */}
      {isNew && (
        <div className="rounded-md border border-blue-100 bg-blue-50 p-4 text-sm text-blue-700">
          Save the plan first, then you can add load steps.
        </div>
      )}

      {/* ── Load Steps ──────────────────────────────────────────────────────── */}
      {!isNew && (
        <Card
          title="Load Steps"
          actions={
            <Button size="sm" onClick={openCreateStep}>
              Add Step
            </Button>
          }
        >
          {sortedSteps.length === 0 ? (
            <div className="py-8 text-center">
              <p className="text-sm text-gray-500">
                No steps yet. Add a step to define what data to load.
              </p>
              <div className="mt-3">
                <Button size="sm" onClick={openCreateStep}>
                  Add Step
                </Button>
              </div>
            </div>
          ) : (
            <div className="divide-y divide-gray-100">
              {sortedSteps.map((step, idx) => {
                const preview = previews[step.id]
                return (
                  <div key={step.id} className="py-4">
                    {/* Step row */}
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex items-start gap-3 min-w-0">
                        <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-100 text-xs font-semibold text-blue-700">
                          {step.sequence}
                        </span>
                        <div className="min-w-0">
                          <p className="font-medium text-gray-900 text-sm">
                            {step.object_name}
                            <span className="ml-2">
                              <Badge variant={operationVariant(step.operation)}>
                                {step.operation}
                              </Badge>
                            </span>
                          </p>
                          <p className="text-xs text-gray-500 mt-0.5 font-mono truncate">
                            {step.csv_file_pattern}
                          </p>
                          {step.external_id_field && (
                            <p className="text-xs text-gray-400 mt-0.5">
                              External ID: {step.external_id_field}
                            </p>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-1 shrink-0">
                        <Button
                          size="sm"
                          variant="ghost"
                          loading={preview?.status === 'loading'}
                          onClick={() => void handlePreviewStep(step)}
                        >
                          Preview
                        </Button>
                        <Button size="sm" variant="secondary" onClick={() => openEditStep(step)}>
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={() => setDeleteStepTarget(step)}
                        >
                          Delete
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={idx === 0 || reorderMutation.isPending}
                          onClick={() => handleMoveUp(step)}
                          aria-label="Move step up"
                        >
                          ↑
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={idx === sortedSteps.length - 1 || reorderMutation.isPending}
                          onClick={() => handleMoveDown(step)}
                          aria-label="Move step down"
                        >
                          ↓
                        </Button>
                      </div>
                    </div>

                    {/* Inline preview result */}
                    {preview && preview.status !== 'idle' && (
                      <div
                        className={clsx(
                          'mt-3 ml-9 rounded-md p-3 text-sm',
                          preview.status === 'loading' && 'bg-gray-50',
                          preview.status === 'success' && 'bg-blue-50',
                          preview.status === 'error' && 'bg-red-50',
                        )}
                      >
                        {preview.status === 'loading' && (
                          <span className="text-gray-500">Loading preview…</span>
                        )}
                        {preview.status === 'error' && (
                          <span className="text-red-700">{preview.message}</span>
                        )}
                        {preview.status === 'success' && (
                          <>
                            <p className="font-medium text-blue-900">
                              {preview.data.matched_files.length} file(s) matched •{' '}
                              {preview.data.total_rows.toLocaleString()} total rows
                            </p>
                            {preview.data.matched_files.map((f) => (
                              <p key={f.filename} className="text-xs text-blue-700 mt-1 font-mono">
                                {f.filename} — {f.row_count.toLocaleString()} rows
                              </p>
                            ))}
                            {preview.data.matched_files.length === 0 && (
                              <p className="text-xs text-amber-600 mt-1">
                                No files matched "{step.csv_file_pattern}"
                              </p>
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </Card>
      )}

      {/* ── Step create / edit modal ─────────────────────────────────────────── */}
      <Modal
        open={stepModalOpen}
        onClose={closeStepModal}
        size="lg"
        title={editingStep ? 'Edit Step' : 'Add Step'}
        footer={
          <>
            <Button variant="secondary" onClick={closeStepModal} disabled={isSavingStep}>
              Cancel
            </Button>
            <Button loading={isSavingStep} onClick={handleSaveStep}>
              {editingStep ? 'Save Changes' : 'Add Step'}
            </Button>
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault()
            handleSaveStep()
          }}
          className="space-y-4"
          noValidate
        >
          {stepFormErrors.length > 0 && (
            <div role="alert" className="rounded border border-red-200 bg-red-50 p-3 space-y-1">
              {stepFormErrors.map((msg, i) => (
                <p key={i} className="text-sm text-red-700">
                  {msg}
                </p>
              ))}
            </div>
          )}

          {/* Salesforce object */}
          <div>
            <label htmlFor="step-object" className={LABEL_CLASS}>
              Salesforce Object <span className="text-red-500">*</span>
            </label>
            <ComboInput
              id="step-object"
              value={stepForm.object_name}
              onChange={(v) => setStepField('object_name', v)}
              options={sfObjects}
              loading={sfObjectsLoading}
              loadingMessage="Loading objects…"
              placeholder="Account"
              inputClassName={INPUT_CLASS}
            />
            {connectionId === '' && (
              <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
                Select a connection on the plan to load object suggestions.
              </p>
            )}
          </div>

          {/* Operation */}
          <div>
            <label htmlFor="step-operation" className={LABEL_CLASS}>
              Operation <span className="text-red-500">*</span>
            </label>
            <select
              id="step-operation"
              value={stepForm.operation}
              onChange={(e) => setStepField('operation', e.target.value)}
              className={INPUT_CLASS}
            >
              {OPERATIONS.map((op) => (
                <option key={op.value} value={op.value}>
                  {op.label}
                </option>
              ))}
            </select>
          </div>

          {/* CSV file pattern */}
          <div>
            <label htmlFor="step-pattern" className={LABEL_CLASS}>
              CSV File Pattern <span className="text-red-500">*</span>
            </label>
            <div className="flex gap-2">
              <input
                id="step-pattern"
                type="text"
                required
                value={stepForm.csv_file_pattern}
                onChange={(e) => {
                  setStepField('csv_file_pattern', e.target.value)
                  setShowFilePicker(false)
                }}
                placeholder="accounts_*.csv"
                className={clsx(INPUT_CLASS, 'font-mono flex-1')}
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setShowFilePicker((v) => !v)}
              >
                Browse
              </Button>
            </div>
            {showFilePicker && (
              <FilePicker
                onSelect={(path) => {
                  setStepField('csv_file_pattern', path)
                  setShowFilePicker(false)
                }}
                onClose={() => setShowFilePicker(false)}
              />
            )}
          </div>

          {/* External ID — shown only for upsert */}
          {stepForm.operation === 'upsert' && (
            <div>
              <label htmlFor="step-ext-id" className={LABEL_CLASS}>
                External ID Field <span className="text-red-500">*</span>
              </label>
              <ComboInput
                id="step-ext-id"
                value={stepForm.external_id_field}
                onChange={(v) => setStepField('external_id_field', v)}
                options={columnHeaders}
                loading={patternPreviewLoading && patternIsLiteral}
                placeholder="ExternalId__c"
                inputClassName={INPUT_CLASS}
              />
              {!patternIsLiteral && stepForm.csv_file_pattern.length > 0 && (
                <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
                  Enter a literal file path (no wildcards) to load column suggestions.
                </p>
              )}
            </div>
          )}

          {/* Partition size */}
          <div>
            <label htmlFor="step-partition" className={LABEL_CLASS}>
              Partition Size
            </label>
            <input
              id="step-partition"
              type="number"
              min="1"
              value={stepForm.partition_size}
              onChange={(e) => setStepField('partition_size', e.target.value)}
              className={INPUT_CLASS}
            />
          </div>

          {/* Assignment Rule ID */}
          <div>
            <label htmlFor="step-assignment-rule" className={LABEL_CLASS}>
              Assignment Rule ID{' '}
              <span className="text-sm text-gray-400 font-normal">(optional)</span>
            </label>
            <input
              id="step-assignment-rule"
              type="text"
              value={stepForm.assignment_rule_id}
              onChange={(e) => setStepField('assignment_rule_id', e.target.value)}
              placeholder="01Q…"
              className={clsx(INPUT_CLASS, 'font-mono')}
            />
          </div>
        </form>
      </Modal>

      {/* ── Delete step confirmation ─────────────────────────────────────────── */}
      <Modal
        open={deleteStepTarget !== null}
        onClose={() => setDeleteStepTarget(null)}
        size="sm"
        title="Delete Step"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setDeleteStepTarget(null)}
              disabled={deleteStepMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={deleteStepMutation.isPending}
              onClick={() => deleteStepTarget && deleteStepMutation.mutate(deleteStepTarget.id)}
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-gray-700">
          Delete the step for{' '}
          <span className="font-semibold">{deleteStepTarget?.object_name}</span>? This cannot be
          undone.
        </p>
      </Modal>

      {/* ── Preflight modal ──────────────────────────────────────────────────── */}
      <Modal
        open={preflightOpen}
        onClose={() => setPreflightOpen(false)}
        size="lg"
        title="Preflight Check"
        footer={<Button onClick={() => setPreflightOpen(false)}>Close</Button>}
      >
        <div className="space-y-4">
          <p className="text-sm text-gray-500">
            Previewing {sortedSteps.length} step(s) — checking file patterns and row counts.
          </p>

          {sortedSteps.map((step) => {
            const preview = previews[step.id]
            return (
              <div key={step.id} className="rounded border border-gray-200 p-3">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-semibold text-gray-500">#{step.sequence}</span>
                  <span className="font-medium text-gray-900 text-sm">{step.object_name}</span>
                  <Badge variant={operationVariant(step.operation)}>{step.operation}</Badge>
                </div>

                {!preview || preview.status === 'idle' ? (
                  <span className="text-xs text-gray-400">Not fetched</span>
                ) : preview.status === 'loading' ? (
                  <div className="flex items-center gap-2">
                    <span
                      aria-label="Loading preview"
                      className="h-4 w-4 rounded-full border-2 border-blue-500 border-t-transparent animate-spin"
                    />
                    <span className="text-xs text-gray-500">Loading…</span>
                  </div>
                ) : preview.status === 'error' ? (
                  <p className="text-xs text-red-700">{preview.message}</p>
                ) : (
                  <>
                    <p className="text-xs font-semibold text-green-700">
                      {preview.data.matched_files.length} file(s) •{' '}
                      {preview.data.total_rows.toLocaleString()} rows
                    </p>
                    {preview.data.matched_files.map((f) => (
                      <p key={f.filename} className="text-xs text-gray-600 font-mono mt-0.5">
                        {f.filename} — {f.row_count.toLocaleString()} rows
                      </p>
                    ))}
                    {preview.data.matched_files.length === 0 && (
                      <p className="text-xs text-amber-600 mt-1">
                        No files matched "{step.csv_file_pattern}"
                      </p>
                    )}
                  </>
                )}
              </div>
            )
          })}
        </div>
      </Modal>
        </>
      )}
    </div>
  )
}
