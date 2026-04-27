import { api, apiFetch, apiPost } from './client'
import type {
  AllSettings,
  CategorySettings,
  SettingsPatch,
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
  InvitationAcceptRequest,
  InvitationAcceptResponse,
  InvitationInfo,
  LoginHistoryEntry,
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
  NotificationSubscription,
  NotificationSubscriptionCreate,
  NotificationSubscriptionUpdate,
  NotificationTestResponse,
  AdminUser,
  AdminUserListResponse,
  InviteUserRequest,
  InviteUserResponse,
  UpdateUserRequest,
  AdminResetPasswordResponse,
  ResendInviteResponse,
  ProfileListItem,
  AdminStatsResponse,
  MfaEnrollStartResponse,
  MfaEnrollConfirmResponse,
  MfaBackupCodesResponse,
  LoginResponse,
  Login2faVerifyRequest,
  Login2faEnrollStartResponse,
  Login2faEnrollAndVerifyRequest,
  Login2faEnrollAndVerifyResponse,
  AdminReset2faResponse,
  AboutPayload,
} from './types'

// ─── Health ──────────────────────────────────────────────────────────────────

export const healthApi = {
  get: () => api.get<{ status: string; env: string }>('/api/health'),
}

// ─── Invitations (SFBL-202) — public, token is the credential ─────────────────

export const invitationsApi = {
  /** Validate an invitation token and return the invitee's email + profile. */
  getInfo: (token: string) => api.get<InvitationInfo>(`/api/invitations/${token}`),
  /** Accept an invitation by setting a password. Returns a JWT on success. */
  accept: (token: string, body: InvitationAcceptRequest) =>
    api.post<InvitationAcceptResponse>(`/api/invitations/${token}/accept`, body),
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
  consecutive_failure_threshold?: number | null
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
  /** Optional user-supplied name. Send raw (incl. empty string); backend trims and coerces empty→NULL. */
  name?: string | null
  csv_file_pattern?: string | null
  soql?: string | null
  partition_size?: number
  external_id_field?: string | null
  assignment_rule_id?: string | null
  input_connection_id?: string | null
  /** SFBL-264: ID of the upstream query step whose output feeds this step. */
  input_from_step_id?: string | null
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
    api.post<void>(`/api/load-plans/${planId}/steps/reorder`, { step_ids: stepIds }),
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
  const { ApiError, getStoredToken, BASE_URL, parseErrorBody } = await import('./client')

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
    const { message, detail, code } = await parseErrorBody(response)
    throw new ApiError({ status: response.status, message, detail, code })
  }

  return response.json() as Promise<EmailTestResponse>
}

// ─── Notification subscriptions (SFBL-182) ────────────────────────────────────

export const notificationSubscriptionsApi = {
  list: () =>
    api.get<NotificationSubscription[]>('/api/notification-subscriptions'),
  create: (data: NotificationSubscriptionCreate) =>
    api.post<NotificationSubscription>('/api/notification-subscriptions', data),
  update: (id: string, data: NotificationSubscriptionUpdate) =>
    api.put<NotificationSubscription>(`/api/notification-subscriptions/${id}`, data),
  delete: (id: string) => api.delete(`/api/notification-subscriptions/${id}`),
  test: (id: string) =>
    api.post<NotificationTestResponse>(`/api/notification-subscriptions/${id}/test`, {}),
}

// ─── Admin users (SFBL-201) ────────────────────────────────────────────────────

export interface AdminUsersListParams {
  status?: string
  include_deleted?: boolean
  page?: number
  page_size?: number
}

export const adminUsersApi = {
  list: (params?: AdminUsersListParams) => {
    const qs = new URLSearchParams()
    if (params?.status) qs.set('status', params.status)
    if (params?.include_deleted) qs.set('include_deleted', 'true')
    if (params?.page) qs.set('page', String(params.page))
    if (params?.page_size) qs.set('page_size', String(params.page_size))
    const qstr = qs.toString()
    return api.get<AdminUserListResponse>(`/api/admin/users${qstr ? `?${qstr}` : ''}`)
  },
  get: (id: string) => api.get<AdminUser>(`/api/admin/users/${id}`),
  invite: (data: InviteUserRequest) => api.post<InviteUserResponse>('/api/admin/users', data),
  update: (id: string, data: UpdateUserRequest) =>
    api.put<AdminUser>(`/api/admin/users/${id}`, data),
  unlock: (id: string) => api.post<AdminUser>(`/api/admin/users/${id}/unlock`),
  deactivate: (id: string) => api.post<AdminUser>(`/api/admin/users/${id}/deactivate`),
  reactivate: (id: string) => api.post<AdminUser>(`/api/admin/users/${id}/reactivate`),
  resetPassword: (id: string) =>
    api.post<AdminResetPasswordResponse>(`/api/admin/users/${id}/reset-password`),
  resendInvite: (id: string) =>
    api.post<ResendInviteResponse>(`/api/admin/users/${id}/resend-invite`),
  /** SFBL-249 / SFBL-251: admin row action — clear a user's 2FA factor. */
  reset2fa: (id: string) =>
    api.post<AdminReset2faResponse>(`/api/admin/users/${id}/reset-2fa`, {}),
  delete: (id: string) => api.delete(`/api/admin/users/${id}`),
  listProfiles: () => api.get<ProfileListItem[]>('/api/admin/profiles'),
  stats: () => api.get<AdminStatsResponse>('/api/admin/users/stats'),
}

export const dependenciesApi = {
  get: () => api.get<DependenciesResponse>('/api/health/dependencies'),
}

// ─── DB-backed settings (SFBL-157) ────────────────────────────────────────────

/**
 * GET /api/settings
 *
 * Returns all categories. We also capture the X-Settings-Cache-TTL header so
 * callers can display the propagation banner; this requires a raw fetch rather
 * than the api.get helper (which only returns the JSON body).
 */
export async function getAllSettings(): Promise<{ data: AllSettings; cacheTtl: number }> {
  const { BASE_URL, getStoredToken, ApiError, parseErrorBody } = await import('./client')
  const headers = new Headers()
  const token = getStoredToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const res = await fetch(`${BASE_URL}/api/settings/`, { headers })
  if (!res.ok) {
    const { message, detail, code } = await parseErrorBody(res)
    throw new ApiError({ status: res.status, message, detail, code })
  }
  const cacheTtl = parseInt(res.headers.get('X-Settings-Cache-TTL') ?? '60', 10)
  const data: AllSettings = await res.json()
  return { data, cacheTtl }
}

/**
 * GET /api/settings/{category}
 *
 * Returns settings for a single category plus the cache TTL.
 */
export async function getSettingsCategory(
  category: string,
): Promise<{ data: CategorySettings; cacheTtl: number }> {
  const { BASE_URL, getStoredToken, ApiError, parseErrorBody } = await import('./client')
  const headers = new Headers()
  const token = getStoredToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const res = await fetch(`${BASE_URL}/api/settings/${category}`, { headers })
  if (!res.ok) {
    const { message, detail, code } = await parseErrorBody(res)
    throw new ApiError({ status: res.status, message, detail, code })
  }
  const cacheTtl = parseInt(res.headers.get('X-Settings-Cache-TTL') ?? '60', 10)
  const data: CategorySettings = await res.json()
  return { data, cacheTtl }
}

/**
 * PATCH /api/settings/{category}
 *
 * Sends only changed fields. Returns updated CategorySettings plus cache TTL.
 * On 422, the ApiError detail will be an array of {field, error} objects.
 */
export async function updateSettingsCategory(
  category: string,
  patch: SettingsPatch,
): Promise<{ data: CategorySettings; cacheTtl: number }> {
  const { BASE_URL, getStoredToken, ApiError, parseErrorBody } = await import('./client')
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const token = getStoredToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const res = await fetch(`${BASE_URL}/api/settings/${category}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(patch),
  })

  if (!res.ok) {
    const { message, detail, code } = await parseErrorBody(res)
    throw new ApiError({ status: res.status, message, detail, code })
  }

  const cacheTtl = parseInt(res.headers.get('X-Settings-Cache-TTL') ?? '60', 10)
  const data: CategorySettings = await res.json()
  return { data, cacheTtl }
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

  getLoginHistory: (limit = 10): Promise<LoginHistoryEntry[]> =>
    api.get<LoginHistoryEntry[]>(`/api/me/login-history?limit=${limit}`),
}

// ─── SFBL-248 / SFBL-251: two-phase login (pre-auth mfa_token bearer) ───────

/**
 * Fetch helper that sends a specific bearer instead of the stored session
 * token. Used for the three `/api/auth/login/2fa*` endpoints, which are gated
 * by the short-lived `mfa_token` minted by phase-1 login — a token the caller
 * never persists to localStorage.
 */
async function apiPostWithBearer<T>(
  path: string,
  bearer: string,
  body: unknown,
): Promise<T> {
  return apiFetch<T>(path, {
    method: 'POST',
    body: JSON.stringify(body),
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${bearer}`,
    },
  })
}

export const loginMfaApi = {
  /**
   * Phase-1 login. Returns either a full `TokenResponse` (no MFA configured)
   * or an `MfaRequiredResponse` with a short-lived `mfa_token` the caller
   * then posts back to `/login/2fa` or `/login/2fa/enroll-and-verify`.
   */
  login: (body: { email: string; password: string }) =>
    apiPost<LoginResponse>('/api/auth/login', body),

  /** POST /api/auth/login/2fa — verify TOTP or backup code. */
  verify: (mfaToken: string, body: Login2faVerifyRequest) =>
    apiPostWithBearer<TokenResponse>('/api/auth/login/2fa', mfaToken, body),

  /** POST /api/auth/login/2fa/enroll/start — forced-enrol QR mint. */
  enrollStart: (mfaToken: string) =>
    apiPostWithBearer<Login2faEnrollStartResponse>(
      '/api/auth/login/2fa/enroll/start',
      mfaToken,
      {},
    ),

  /** POST /api/auth/login/2fa/enroll-and-verify — forced-enrol confirm. */
  enrollAndVerify: (mfaToken: string, body: Login2faEnrollAndVerifyRequest) =>
    apiPostWithBearer<Login2faEnrollAndVerifyResponse>(
      '/api/auth/login/2fa/enroll-and-verify',
      mfaToken,
      body,
    ),
}

// ─── SFBL-250: self-service 2FA enrolment + management ──────────────────────

export const mfaApi = {
  /** Begin TOTP enrolment — returns the secret, otpauth URI, and QR SVG. */
  enrollStart: (): Promise<MfaEnrollStartResponse> =>
    api.post<MfaEnrollStartResponse>('/api/auth/2fa/enroll/start', {}),

  /**
   * Confirm the first TOTP code and persist the factor. Returns a fresh
   * access token (caller MUST replace the stored token) and the one-shot
   * backup-code set.
   */
  enrollConfirm: (body: { secret_base32: string; code: string }): Promise<MfaEnrollConfirmResponse> =>
    api.post<MfaEnrollConfirmResponse>('/api/auth/2fa/enroll/confirm', body),

  /** Rotate the backup-code set — requires a valid current TOTP code. */
  regenerateBackupCodes: (body: { code: string }): Promise<MfaBackupCodesResponse> =>
    api.post<MfaBackupCodesResponse>('/api/auth/2fa/backup-codes/regenerate', body),

  /**
   * Disable the user's own 2FA factor. Requires password + current TOTP
   * code. Returns 403 `tenant_enforced` when the tenant-wide `require_2fa`
   * setting is on.
   */
  disable: (body: { password: string; code: string }): Promise<void> =>
    api.post<void>('/api/auth/2fa/disable', body),
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

// ─── About / system info (SFBL-269) ──────────────────────────────────────────

export function getAbout(): Promise<AboutPayload> {
  return api.get<AboutPayload>('/api/admin/about')
}
