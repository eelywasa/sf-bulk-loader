import { apiGet, apiPost, apiPut, apiDelete } from './client'
import type {
  Connection,
  ConnectionCreate,
  ConnectionUpdate,
  ConnectionTestResponse,
  LoadPlan,
  LoadPlanDetail,
  LoadPlanCreate,
  LoadPlanUpdate,
  LoadStep,
  LoadStepCreate,
  LoadStepUpdate,
  LoadRun,
  RunListFilters,
  JobRecord,
  JobListFilters,
  StepPreviewResponse,
  InputFileInfo,
  InputFilePreview,
  HealthResponse,
} from './types'

// ─── Connections ──────────────────────────────────────────────────────────────

export const connectionsApi = {
  list: (): Promise<Connection[]> =>
    apiGet('/api/connections/'),

  create: (data: ConnectionCreate): Promise<Connection> =>
    apiPost('/api/connections/', data),

  update: (id: string, data: ConnectionUpdate): Promise<Connection> =>
    apiPut(`/api/connections/${id}`, data),

  delete: (id: string): Promise<void> =>
    apiDelete(`/api/connections/${id}`),

  test: (id: string): Promise<ConnectionTestResponse> =>
    apiPost(`/api/connections/${id}/test`),
}

// ─── Load Plans ───────────────────────────────────────────────────────────────

export const plansApi = {
  list: (): Promise<LoadPlan[]> =>
    apiGet('/api/load-plans/'),

  create: (data: LoadPlanCreate): Promise<LoadPlan> =>
    apiPost('/api/load-plans/', data),

  get: (planId: string): Promise<LoadPlanDetail> =>
    apiGet(`/api/load-plans/${planId}`),

  update: (planId: string, data: LoadPlanUpdate): Promise<LoadPlan> =>
    apiPut(`/api/load-plans/${planId}`, data),

  delete: (planId: string): Promise<void> =>
    apiDelete(`/api/load-plans/${planId}`),

  /** Start a new run for this plan. Returns the created LoadRun. */
  startRun: (planId: string): Promise<LoadRun> =>
    apiPost(`/api/load-plans/${planId}/run`),
}

// ─── Load Steps ───────────────────────────────────────────────────────────────

export const stepsApi = {
  create: (planId: string, data: LoadStepCreate): Promise<LoadStep> =>
    apiPost(`/api/load-plans/${planId}/steps`, data),

  update: (planId: string, stepId: string, data: LoadStepUpdate): Promise<LoadStep> =>
    apiPut(`/api/load-plans/${planId}/steps/${stepId}`, data),

  delete: (planId: string, stepId: string): Promise<void> =>
    apiDelete(`/api/load-plans/${planId}/steps/${stepId}`),

  /** Reorder steps by providing an ordered array of step IDs. */
  reorder: (planId: string, stepIds: string[]): Promise<LoadStep[]> =>
    apiPost(`/api/load-plans/${planId}/steps/reorder`, stepIds),

  /** Preview CSV file discovery and row counts for one step. */
  preview: (planId: string, stepId: string): Promise<StepPreviewResponse> =>
    apiPost(`/api/load-plans/${planId}/steps/${stepId}/preview`),
}

// ─── Runs ─────────────────────────────────────────────────────────────────────

export const runsApi = {
  list: (filters?: RunListFilters): Promise<LoadRun[]> => {
    const params = new URLSearchParams()
    if (filters?.plan_id) params.set('plan_id', filters.plan_id)
    if (filters?.run_status) params.set('run_status', filters.run_status)
    if (filters?.started_after) params.set('started_after', filters.started_after)
    if (filters?.started_before) params.set('started_before', filters.started_before)
    const qs = params.toString()
    return apiGet(`/api/runs/${qs ? `?${qs}` : ''}`)
  },

  get: (runId: string): Promise<LoadRun> =>
    apiGet(`/api/runs/${runId}`),

  jobs: (runId: string, filters?: JobListFilters): Promise<JobRecord[]> => {
    const params = new URLSearchParams()
    if (filters?.step_id) params.set('step_id', filters.step_id)
    if (filters?.job_status) params.set('job_status', filters.job_status)
    const qs = params.toString()
    return apiGet(`/api/runs/${runId}/jobs${qs ? `?${qs}` : ''}`)
  },

  abort: (runId: string): Promise<LoadRun> =>
    apiPost(`/api/runs/${runId}/abort`),
}

// ─── Jobs ─────────────────────────────────────────────────────────────────────

export const jobsApi = {
  get: (jobId: string): Promise<JobRecord> =>
    apiGet(`/api/jobs/${jobId}`),

  /** Returns a URL suitable for an <a href> download link (not buffered). */
  successCsvUrl: (jobId: string): string =>
    `/api/jobs/${jobId}/success-csv`,

  errorCsvUrl: (jobId: string): string =>
    `/api/jobs/${jobId}/error-csv`,

  unprocessedCsvUrl: (jobId: string): string =>
    `/api/jobs/${jobId}/unprocessed-csv`,
}

// ─── Files ────────────────────────────────────────────────────────────────────

export const filesApi = {
  listInput: (): Promise<InputFileInfo[]> =>
    apiGet('/api/files/input'),

  previewInput: (filename: string, rows = 25): Promise<InputFilePreview> =>
    apiGet(`/api/files/input/${encodeURIComponent(filename)}/preview?rows=${rows}`),
}

// ─── Health ───────────────────────────────────────────────────────────────────

export const healthApi = {
  get: (): Promise<HealthResponse> =>
    apiGet('/api/health'),
}
