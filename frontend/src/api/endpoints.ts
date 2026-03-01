import { api } from './client'
import type {
  Connection,
  ConnectionCreate,
  ConnectionTestResponse,
  LoadPlan,
  LoadPlanDetail,
  LoadStep,
  LoadRun,
  JobRecord,
  StepPreviewResponse,
  InputFileInfo,
  InputFilePreview,
} from './types'

// ─── Health ──────────────────────────────────────────────────────────────────

export const healthApi = {
  get: () => api.get<{ status: string; env: string }>('/api/health'),
}

// ─── Connections ──────────────────────────────────────────────────────────────

export const connectionsApi = {
  list: () => api.get<Connection[]>('/api/connections/'),
  create: (data: ConnectionCreate) => api.post<Connection>('/api/connections/', data),
  update: (id: string, data: Partial<ConnectionCreate>) =>
    api.put<Connection>(`/api/connections/${id}`, data),
  delete: (id: string) => api.delete(`/api/connections/${id}`),
  test: (id: string) => api.post<ConnectionTestResponse>(`/api/connections/${id}/test`),
}

// ─── Load Plans ───────────────────────────────────────────────────────────────

export interface LoadPlanCreate {
  connection_id: string
  name: string
  description?: string | null
  abort_on_step_failure?: boolean
  error_threshold_pct?: number
  max_parallel_jobs?: number
}

export const plansApi = {
  list: () => api.get<LoadPlan[]>('/api/load-plans/'),
  get: (id: string) => api.get<LoadPlanDetail>(`/api/load-plans/${id}`),
  create: (data: LoadPlanCreate) => api.post<LoadPlan>('/api/load-plans/', data),
  update: (id: string, data: Partial<LoadPlanCreate>) =>
    api.put<LoadPlan>(`/api/load-plans/${id}`, data),
  delete: (id: string) => api.delete(`/api/load-plans/${id}`),
  startRun: (id: string) => api.post<LoadRun>(`/api/load-plans/${id}/run`),
}

// ─── Load Steps ───────────────────────────────────────────────────────────────

export interface LoadStepCreate {
  object_name: string
  operation: string
  csv_file_pattern: string
  partition_size?: number
  external_id_field?: string | null
  assignment_rule_id?: string | null
  sequence?: number
}

export const stepsApi = {
  create: (planId: string, data: LoadStepCreate) =>
    api.post<LoadStep>(`/api/load-plans/${planId}/steps`, data),
  update: (planId: string, stepId: string, data: Partial<LoadStepCreate>) =>
    api.put<LoadStep>(`/api/load-plans/${planId}/steps/${stepId}`, data),
  delete: (planId: string, stepId: string) =>
    api.delete(`/api/load-plans/${planId}/steps/${stepId}`),
  reorder: (planId: string, stepIds: string[]) =>
    api.post<void>(`/api/load-plans/${planId}/steps/reorder`, stepIds),
  preview: (planId: string, stepId: string) =>
    api.post<StepPreviewResponse>(`/api/load-plans/${planId}/steps/${stepId}/preview`),
}

// ─── Runs ─────────────────────────────────────────────────────────────────────

export interface RunListParams {
  plan_id?: string
  run_status?: string
  started_after?: string
  started_before?: string
}

function buildQs(params?: Record<string, string | undefined>): string {
  if (!params) return ''
  const filtered = Object.entries(params).filter(
    (entry): entry is [string, string] => entry[1] != null,
  )
  if (!filtered.length) return ''
  return '?' + new URLSearchParams(filtered).toString()
}

export const runsApi = {
  list: (params?: RunListParams) =>
    api.get<LoadRun[]>(`/api/runs/${buildQs(params as Record<string, string | undefined>)}`),
  get: (id: string) => api.get<LoadRun>(`/api/runs/${id}`),
  abort: (id: string) => api.post<void>(`/api/runs/${id}/abort`),
  jobs: (runId: string, params?: { step_id?: string; job_status?: string }) =>
    api.get<JobRecord[]>(
      `/api/runs/${runId}/jobs${buildQs(params as Record<string, string | undefined>)}`,
    ),
}

// ─── Jobs ─────────────────────────────────────────────────────────────────────

export const jobsApi = {
  get: (id: string) => api.get<JobRecord>(`/api/jobs/${id}`),
  successCsvUrl: (id: string) => `/api/jobs/${id}/success-csv`,
  errorCsvUrl: (id: string) => `/api/jobs/${id}/error-csv`,
  unprocessedCsvUrl: (id: string) => `/api/jobs/${id}/unprocessed-csv`,
}

// ─── Files ────────────────────────────────────────────────────────────────────

export const filesApi = {
  list: () => api.get<InputFileInfo[]>('/api/files/input'),
  preview: (filename: string, rows = 25) =>
    api.get<InputFilePreview>(
      `/api/files/input/${encodeURIComponent(filename)}/preview?rows=${rows}`,
    ),
}
