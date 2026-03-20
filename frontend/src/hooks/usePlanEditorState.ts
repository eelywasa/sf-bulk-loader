import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useToast } from '../components/ui/Toast'
import {
  plansApi,
  stepsApi,
  connectionsApi,
  inputConnectionsApi,
  filesApi,
  type LoadPlanCreate,
  type LoadStepCreate,
} from '../api/endpoints'
import type { LoadStep } from '../api/types'
import {
  EMPTY_PLAN_FORM,
  EMPTY_STEP_FORM,
  extractErrors,
  type PlanFormData,
  type StepFormData,
} from '../pages/planEditorUtils'

export function usePlanEditorState(id: string | undefined) {
  const isNew = id === 'new'
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const toast = useToast()

  // ── Plan form state ──────────────────────────────────────────────────────────

  const [form, setForm] = useState<PlanFormData>(EMPTY_PLAN_FORM)
  const [formErrors, setFormErrors] = useState<string[]>([])

  // ── Step modal state ─────────────────────────────────────────────────────────

  const [stepModalOpen, setStepModalOpen] = useState(false)
  const [editingStep, setEditingStep] = useState<LoadStep | null>(null)
  const [stepForm, setStepForm] = useState<StepFormData>(EMPTY_STEP_FORM)
  const [stepFormErrors, setStepFormErrors] = useState<string[]>([])
  const [deleteStepTarget, setDeleteStepTarget] = useState<LoadStep | null>(null)

  // ── File picker state ────────────────────────────────────────────────────────

  const [showFilePicker, setShowFilePicker] = useState(false)

  // ── Derived state ────────────────────────────────────────────────────────────

  const patternIsLiteral =
    stepModalOpen &&
    stepForm.operation === 'upsert' &&
    stepForm.csv_file_pattern.length > 0 &&
    !stepForm.csv_file_pattern.includes('*') &&
    !stepForm.csv_file_pattern.includes('?')

  // ── Queries ──────────────────────────────────────────────────────────────────

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

  const { data: inputConnections = [] } = useQuery({
    queryKey: ['input-connections'],
    queryFn: inputConnectionsApi.list,
    enabled: stepModalOpen,
  })

  const connectionId = form.connection_id || plan?.connection_id || ''
  const stepSource = stepForm.input_connection_id || 'local'

  const { data: sfObjects = [], isLoading: sfObjectsLoading } = useQuery({
    queryKey: ['connections', connectionId, 'objects'],
    queryFn: () => connectionsApi.listObjects(connectionId),
    enabled: stepModalOpen && connectionId !== '',
    staleTime: 5 * 60 * 1000,
  })

  const { data: patternPreview, isLoading: patternPreviewLoading } = useQuery({
    queryKey: ['files', 'preview-headers', stepSource, stepForm.csv_file_pattern],
    queryFn: () => filesApi.previewInput(stepForm.csv_file_pattern, 1, stepSource),
    enabled: patternIsLiteral,
  })

  const columnHeaders: string[] = patternPreview?.header ?? []

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

  // ── Mutations ────────────────────────────────────────────────────────────────

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

  function openStepModal(step?: LoadStep) {
    if (step) {
      setEditingStep(step)
      setStepForm({
        object_name: step.object_name,
        operation: step.operation,
        csv_file_pattern: step.csv_file_pattern,
        partition_size: String(step.partition_size),
        external_id_field: step.external_id_field ?? '',
        assignment_rule_id: step.assignment_rule_id ?? '',
        input_connection_id: step.input_connection_id ?? '',
      })
    } else {
      setEditingStep(null)
      setStepForm(EMPTY_STEP_FORM)
    }
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

  function handleInputSourceChange(value: string) {
    queryClient.removeQueries({ queryKey: ['files', 'preview-headers'] })
    setStepForm((prev) => ({
      ...prev,
      input_connection_id: value,
      csv_file_pattern: '',
      external_id_field: '',
    }))
    setShowFilePicker(false)
  }

  function handleSaveStep() {
    setStepFormErrors([])
    if (stepForm.operation === 'upsert' && !stepForm.external_id_field.trim()) {
      setStepFormErrors(['External ID Field is required for upsert operations.'])
      return
    }
    const data: LoadStepCreate = {
      object_name: stepForm.object_name,
      operation: stepForm.operation,
      csv_file_pattern: stepForm.csv_file_pattern,
      partition_size: Number(stepForm.partition_size),
      external_id_field: stepForm.external_id_field || null,
      assignment_rule_id: stepForm.assignment_rule_id || null,
      input_connection_id: stepForm.input_connection_id || null,
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

  const isSavingPlan = createPlanMutation.isPending || updatePlanMutation.isPending
  const isSavingStep = createStepMutation.isPending || updateStepMutation.isPending

  return {
    // Plan form
    form,
    formErrors,
    setPlanField,
    handleSavePlan,
    isSavingPlan,
    // Step modal
    stepModalOpen,
    editingStep,
    stepForm,
    stepFormErrors,
    showFilePicker,
    setShowFilePicker,
    deleteStepTarget,
    setDeleteStepTarget,
    openStepModal,
    closeStepModal,
    setStepField,
    handleInputSourceChange,
    handleSaveStep,
    isSavingStep,
    // File picker / column headers
    patternIsLiteral,
    columnHeaders,
    patternPreviewLoading,
    connectionId,
    // Queries
    plan,
    planLoading,
    planError,
    connections,
    inputConnections,
    sfObjects,
    sfObjectsLoading,
    // Step order / reorder
    sortedSteps,
    handleMoveUp,
    handleMoveDown,
    reorderMutation,
    // Start run
    startRunMutation,
    // Delete step
    deleteStepMutation,
  }
}
