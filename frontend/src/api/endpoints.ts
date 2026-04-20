import { api, apiFetch } from './client'
import type {
  Connection,
  ConnectionCreate,
  CsvFetchParams,
  ConnectionTestResponse,
  DependenciesResponse,
  EmailTestRequest,
  EmailTestResponse,
  EmailTestRenderFailure,
  InputConnection,
  InputConnectionCreate,
  InputConnectionTestResponse,
  LoadPlan,
  LoadPlanDetail,
  LoadStep,
  LoadRun,
  JobRecord,
  StepPreviewResponse,
  ValidateSoqlResponse,
  InputFilePreview,
  InputDirectoryEntry,
  UserResponse,
  TokenResponse,
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
  listObjects: (id: string) => api.get<string[]>(`/api/connections/${id}/objects`),
}

export const inputConnectionsApi = {
  list: (params?: { direction?: string }) => {
    const qs = params?.direction ? `?direction=${encodeURIComponent(params.direction)}` : ''
    return api.get<InputConnection[]>(`/api/input-connections/${qs}`)
  },
  create: (data: InputConnectionCreate) => api.post<InputConnection>('/api/input-connections/', data),
  update: (id: string, data: Partial<InputConnectionCreate>) =>
    api.put<InputConnection>(`/api/input-connections/${id}`, data),
  delete: (id: string) => api.delete(`/api/input-connections/${id}`),
  test: (id: string) => api.post<InputConnectionTestResponse>(`/api/input-connections/${id}/test`),
}

// ─── Load Plans ───────────────────────────────────────────────────────────────

export interface LoadPlanCreate {
  connection_id: string
  name: string
  description?: string | null
  abort_on_step_failure?: boolean
  error_threshold_pct?: number
  max_parallel_jobs?: number
  output_connection_id?: string | null
}

export const plansApi = {
  list: () => api.get<LoadPlan[]>('/api/load-plans/'),
  get: (id: string) => api.get<LoadPlanDetail>(`/api/load-plans/${id}`),
  create: (data: LoadPlanCreate) => api.post<LoadPlan>('/api/load-plans/', data),
  update: (id: string, data: Partial<LoadPlanCreate>) =>
    api.put<LoadPlan>(`/api/load-plans/${id}`, data),
  delete: (id: string) => api.delete(`/api/load-plans/${id}`),
  duplicate: (id: string) => api.post<LoadPlanDetail>(`/api/load-plans/${id}/duplicate`, {}),
  startRun: (id: string) => api.post<LoadRun>(`/api/load-plans/${id}/run`, {}),
}

// ─── Load Steps ───────────────────────────────────────────────────────────────

export interface LoadStepCreate {
  object_name: string
  operation: string
  csv_file_pattern?: string | null
  soql?: string | null
  partition_size?: number
  external_id_field?: string | null
  assignment_rule_id?: string | null
  input_connection_id?: string | null
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
  validateSoql: (planId: string, soql: string) =>
    api.post<ValidateSoqlResponse>(`/api/load-plans/${planId}/validate-soql`, { soql }),
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
  retryStep: (runId: string, stepId: string) =>
    api.post<LoadRun>(`/api/runs/${runId}/retry-step/${stepId}`, {}),
  logsZipUrl: (id: string, opts: { success: boolean; errors: boolean; unprocessed: boolean }) => {
    const params = new URLSearchParams({
      success: String(opts.success),
      errors: String(opts.errors),
      unprocessed: String(opts.unprocessed),
    })
    return `/api/runs/${id}/logs.zip?${params}`
  },
}

// ─── Jobs ─────────────────────────────────────────────────────────────────────

export const jobsApi = {
  get: (id: string) => api.get<JobRecord>(`/api/jobs/${id}`),
  successCsvUrl: (id: string) => `/api/jobs/${id}/success-csv`,
  errorCsvUrl: (id: string) => `/api/jobs/${id}/error-csv`,
  unprocessedCsvUrl: (id: string) => `/api/jobs/${id}/unprocessed-csv`,
  previewSuccessCsv: (id: string, params?: CsvFetchParams) =>
    api.get<InputFilePreview>(`/api/jobs/${id}/success-csv/preview?${buildPreviewQuery(params)}`),
  previewErrorCsv: (id: string, params?: CsvFetchParams) =>
    api.get<InputFilePreview>(`/api/jobs/${id}/error-csv/preview?${buildPreviewQuery(params)}`),
  previewUnprocessedCsv: (id: string, params?: CsvFetchParams) =>
    api.get<InputFilePreview>(`/api/jobs/${id}/unprocessed-csv/preview?${buildPreviewQuery(params)}`),
}

// ─── Admin email ──────────────────────────────────────────────────────────────

/**
 * Error thrown when the template renders but produces an unsafe subject.
 * The `code` field is the stable EmailRenderError code from the backend.
 * The caller should display it verbatim — do NOT translate it.
 */
export class EmailRenderFailureError extends Error {
  readonly code: string

  constructor(failure: EmailTestRenderFailure) {
    super(failure.message)
    this.name = 'EmailRenderFailureError'
    this.code = failure.code
  }
}

/**
 * POST /api/admin/email/test
 *
 * Sends a test email and returns the typed delivery result. On a 422 render
 * failure, throws EmailRenderFailureError (caller should display `.code`
 * verbatim). On other non-2xx responses, the base apiFetch throws ApiError.
 *
 * We cannot use apiFetch directly because that helper throws on 422 and
 * loses the structured body. We perform a raw fetch here, reusing the
 * token-injection logic from client.ts.
 */
export async function postEmailTest(req: EmailTestRequest): Promise<EmailTestResponse> {
  const { ApiError, getStoredToken, BASE_URL } = await import('./client')

  const headers = new Headers({ 'Content-Type': 'application/json' })
  const token = getStoredToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${BASE_URL}/api/admin/email/test`, {
    method: 'POST',
    headers,
    body: JSON.stringify(req),
  })

  if (response.status === 422) {
    const body: EmailTestRenderFailure = await response.json()
    throw new EmailRenderFailureError(body)
  }

  if (!response.ok) {
    let message = response.statusText || `HTTP ${response.status}`
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      // ignore
    }
    throw new ApiError({ status: response.status, message })
  }

  return response.json() as Promise<EmailTestResponse>
}

export const dependenciesApi = {
  get: () => api.get<DependenciesResponse>('/api/health/dependencies'),
}

// ─── SFBL-150: public auth (forgot password, reset password) ───

export function requestPasswordReset(body: { email: string }): Promise<void> {
  return apiFetch<void>('/api/auth/password-reset/request', {
    method: 'POST',
    body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' },
  })
}

export function confirmPasswordReset(body: {
  token: string
  new_password: string
}): Promise<void> {
  return apiFetch<void>('/api/auth/password-reset/confirm', {
    method: 'POST',
    body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' },
  })
}

// ─── Files ────────────────────────────────────────────────────────────────────

function buildPreviewQuery(params?: CsvFetchParams): string {
  const query = new URLSearchParams()
  const normalized = params ?? { offset: 0, limit: 50, filters: [] }

  query.set('limit', String(normalized.limit))
  query.set('offset', String(normalized.offset))
  if (normalized.filters.length > 0) {
    query.set('filters', JSON.stringify(normalized.filters))
  }

  return query.toString()
}

function buildPreviewPath(filePath: string): string {
  return filePath.split('/').map(encodeURIComponent).join('/')
}

function previewInput(
  filePath: string,
  params?: CsvFetchParams,
  source?: string,
): Promise<InputFilePreview> {
  const query = new URLSearchParams(buildPreviewQuery(params))
  const effectiveSource = source ?? 'local'
  if (effectiveSource !== 'local') {
    query.set('source', effectiveSource)
  }

  return api.get<InputFilePreview>(
    `/api/files/input/${buildPreviewPath(filePath)}/preview?${query.toString()}`,
  )
}

// ─── SFBL-149: me (profile, email change, password change) ───────────────────

export const meApi = {
  updateProfile: (body: { display_name: string }): Promise<UserResponse> =>
    api.put<UserResponse>('/api/me', body),

  changePassword: (body: {
    current_password: string
    new_password: string
  }): Promise<TokenResponse> => api.post<TokenResponse>('/api/me/password', body),

  requestEmailChange: (body: { new_email: string }): Promise<void> =>
    api.post<void>('/api/me/email-change/request', body),

  confirmEmailChange: (body: { token: string }): Promise<void> =>
    api.post<void>('/api/me/email-change/confirm', body),
}

// ─── Files ────────────────────────────────────────────────────────────────────

export const filesApi = {
  listInput: (path = '', source = 'local') => {
    // SFBL-178: the "local-output" sentinel browses the output tree via the
    // dedicated endpoint instead of the input one.
    if (source === 'local-output') {
      return filesApi.listOutput(path)
    }
    const params = new URLSearchParams()
    if (path) params.set('path', path)
    if (source !== 'local') params.set('source', source)
    const qs = params.toString()
    return api.get<InputDirectoryEntry[]>(`/api/files/input${qs ? `?${qs}` : ''}`)
  },
  previewInput,
  listOutput: (path = '') => {
    const qs = path ? `?path=${encodeURIComponent(path)}` : ''
    return api.get<InputDirectoryEntry[]>(`/api/files/output${qs}`)
  },
  previewOutput: (filePath: string, params?: CsvFetchParams): Promise<InputFilePreview> => {
    const query = new URLSearchParams(buildPreviewQuery(params))
    return api.get<InputFilePreview>(
      `/api/files/output/${buildPreviewPath(filePath)}/preview?${query.toString()}`,
    )
  },
}
