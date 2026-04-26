// ─── Runtime config ────────────────────────────────────────────────────────────

export interface RuntimeConfig {
  auth_mode: 'none' | 'local'
  app_distribution: string
  transport_mode: string
  input_storage_mode: string
}

// ─── Auth ──────────────────────────────────────────────────────────────────────

// ─── Invitation accept (SFBL-202) ─────────────────────────────────────────────

export interface InvitationInfo {
  email: string
  display_name: string | null
  profile_name: string | null
}

export interface InvitationAcceptRequest {
  password: string
}

export interface InvitationAcceptResponse {
  access_token: string
  token_type: string
}

export interface LoginHistoryEntry {
  attempted_at: string  // ISO-8601 datetime string
  ip: string
  outcome: 'Success' | 'Failed'
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  /** SFBL-190: set when the user must change their password before continuing. */
  must_reset_password?: boolean
  /** SFBL-248: explicit false on the full-token branch of the login union. */
  mfa_required?: false
}

/**
 * SFBL-248 / SFBL-251: phase-1 login response when the user must complete a
 * second factor (either verify or forced-enrol).
 */
export interface MfaRequiredResponse {
  mfa_required: true
  mfa_token: string
  mfa_methods: string[]
  must_enroll: boolean
}

/** Discriminated union returned by `POST /api/auth/login`. */
export type LoginResponse = TokenResponse | MfaRequiredResponse

/** Type guard for the MFA-required branch of the login response. */
export function isMfaRequired(resp: LoginResponse): resp is MfaRequiredResponse {
  return (resp as MfaRequiredResponse).mfa_required === true
}

// ─── 2FA login challenge / forced enrol (SFBL-251) ───────────────────────────

export interface Login2faVerifyRequest {
  method: 'totp' | 'backup_code'
  code: string
}

/** Shape of `POST /api/auth/login/2fa/enroll/start`. */
export interface Login2faEnrollStartResponse {
  secret_base32: string
  otpauth_uri: string
  qr_svg: string
}

export interface Login2faEnrollAndVerifyRequest {
  secret_base32: string
  code: string
}

/** Full access token plus one-shot backup codes from the forced-enrol path. */
export interface Login2faEnrollAndVerifyResponse {
  access_token: string
  token_type: string
  expires_in: number
  must_reset_password?: boolean
  mfa_required?: false
  backup_codes: string[]
}

/** Admin row-action response from `POST /api/admin/users/{id}/reset-2fa`. */
export interface AdminReset2faResponse {
  user_id: string
  had_factor: boolean
  backup_codes_cleared: number
}

export interface UserProfile {
  name: 'admin' | 'operator' | 'viewer' | 'desktop' | string
}

/**
 * 2FA enrolment status for the current user (SFBL-246 / SFBL-244). Always
 * present on the /me response; `enrolled_at` is null and
 * `backup_codes_remaining` is 0 when the user has no factor configured.
 */
export interface MfaStatus {
  enrolled: boolean
  enrolled_at: string | null
  backup_codes_remaining: number
  /**
   * True when the tenant-wide `require_2fa` setting is on, meaning the user
   * cannot self-disable (spec D8). Flipped from optional to required in
   * SFBL-251 — the backend always emits the field now.
   */
  tenant_required: boolean
}

// ─── 2FA self-service (SFBL-250) ──────────────────────────────────────────────

export interface MfaEnrollStartResponse {
  secret_base32: string
  otpauth_uri: string
  qr_svg: string
}

export interface MfaEnrollConfirmRequest {
  secret_base32: string
  code: string
}

export interface MfaEnrollConfirmResponse {
  access_token: string
  token_type: string
  expires_in: number
  backup_codes: string[]
}

export interface MfaBackupCodesResponse {
  backup_codes: string[]
}

export interface MfaRegenerateRequest {
  code: string
}

export interface MfaDisableRequest {
  password: string
  code: string
}

export interface UserResponse {
  id: string
  email: string
  display_name: string | null
  is_active?: boolean
  is_admin?: boolean
  profile?: UserProfile
  /** Sorted list of permission keys granted to this user. */
  permissions?: string[]
  /** 2FA status (SFBL-246). Optional for forward-compat with older backends. */
  mfa?: MfaStatus
}

// ─── Status enums ─────────────────────────────────────────────────────────────

export type RunStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'completed_with_errors'
  | 'failed'
  | 'aborted'

export type JobStatus =
  | 'pending'
  | 'uploading'
  | 'upload_complete'
  | 'in_progress'
  | 'job_complete'
  | 'failed'
  | 'aborted'

export type Operation = 'insert' | 'update' | 'upsert' | 'delete' | 'query' | 'queryAll'

export function isQueryOperation(op: Operation): boolean {
  return op === 'query' || op === 'queryAll'
}

export function operationLabel(op: Operation): string {
  if (op === 'queryAll') return 'Query All (incl. deleted)'
  if (op === 'query') return 'Query'
  return op
}

// ─── API error types ───────────────────────────────────────────────────────────

export interface ApiValidationError {
  type: string
  loc: Array<string | number>
  msg: string
  input?: unknown
}

export interface ApiErrorShape {
  status: number
  message: string
  detail?: string | ApiValidationError[]
  code?: string
}

// ─── Connections ───────────────────────────────────────────────────────────────

export interface Connection {
  id: string
  name: string
  instance_url: string
  login_url: string
  client_id: string
  username: string
  is_sandbox: boolean
  created_at: string
  updated_at: string
}

export interface ConnectionCreate {
  name: string
  instance_url: string
  login_url: string
  client_id: string
  private_key: string
  username: string
  is_sandbox?: boolean
}

export interface ConnectionTestResponse {
  success: boolean
  message: string
  instance_url?: string | null
}

export interface InputConnection {
  id: string
  name: string
  provider: string
  bucket: string
  root_prefix?: string | null
  region?: string | null
  direction: 'in' | 'out' | 'both'
  created_at: string
  updated_at: string
}

export interface InputConnectionCreate {
  name: string
  provider: string
  bucket: string
  root_prefix?: string | null
  region?: string | null
  access_key_id: string
  secret_access_key: string
  session_token?: string | null
  direction?: 'in' | 'out' | 'both'
}

export interface InputConnectionTestResponse {
  success: boolean
  message: string
}

// ─── Load Plans + Steps ────────────────────────────────────────────────────────

export interface LoadStep {
  id: string
  load_plan_id: string
  sequence: number
  object_name: string
  operation: Operation
  /** Optional user-supplied name for this step. Null when not set. */
  name?: string | null
  external_id_field?: string | null
  csv_file_pattern?: string | null
  soql?: string | null
  partition_size: number
  assignment_rule_id?: string | null
  input_connection_id?: string | null
  /** SFBL-264: ID of the upstream query step whose output feeds this step. */
  input_from_step_id?: string | null
  created_at: string
  updated_at: string
}

export interface LoadPlan {
  id: string
  connection_id: string
  name: string
  description?: string | null
  abort_on_step_failure: boolean
  error_threshold_pct: number
  max_parallel_jobs: number
  consecutive_failure_threshold?: number | null
  output_connection_id: string | null
  created_at: string
  updated_at: string
}

export interface LoadPlanDetail extends LoadPlan {
  load_steps: LoadStep[]
}

// ─── Runs ──────────────────────────────────────────────────────────────────────

export interface PreflightWarning {
  step_id: string
  outcome_code: string
  error: string
}

export interface RunErrorSummary {
  auth_error?: string | null
  storage_error?: string | null
  circuit_breaker?: string | null
  preflight_warnings?: PreflightWarning[] | null
}

export interface LoadRun {
  id: string
  load_plan_id: string
  status: RunStatus
  started_at?: string | null
  completed_at?: string | null
  total_records?: number | null
  total_success?: number | null
  total_errors?: number | null
  initiated_by?: string | null
  error_summary?: RunErrorSummary | null
  retry_of_run_id?: string | null
  is_retry: boolean
}

// ─── Jobs ──────────────────────────────────────────────────────────────────────

export interface JobRecord {
  id: string
  load_run_id: string
  load_step_id: string
  sf_job_id?: string | null
  sf_instance_url?: string | null
  partition_index: number
  status: JobStatus
  records_processed?: number | null
  records_failed?: number | null
  records_successful?: number | null
  total_records?: number | null
  success_file_path?: string | null
  error_file_path?: string | null
  unprocessed_file_path?: string | null
  sf_api_response?: string | null
  started_at?: string | null
  completed_at?: string | null
  error_message?: string | null
}

// ─── Preview / Files ───────────────────────────────────────────────────────────

export interface StepPreviewInfo {
  filename: string
  row_count: number
}

export interface StepPreviewQueryPlan {
  leadingOperation: string
  sobjectType: string
  [key: string]: unknown
}

export interface ValidateSoqlResponse {
  valid: boolean
  plan?: StepPreviewQueryPlan | null
  error?: string | null
}

export interface StepPreviewResponse {
  pattern?: string | null
  matched_files: StepPreviewInfo[]
  total_rows: number
  kind?: 'dml' | 'query'
  note?: string | null
  // Query-op explain fields (present only when kind="query")
  valid?: boolean | null
  plan?: StepPreviewQueryPlan | null
  error?: string | null
}

export interface InputFileInfo {
  filename: string
  size_bytes: number
}

export type EntryKind = 'file' | 'directory'

export interface InputDirectoryEntry {
  name: string
  kind: EntryKind
  path: string
  size_bytes: number | null
  row_count: number | null
  source?: string
  provider?: string
}

export interface FilterRule {
  column: string
  value: string
}

export interface CsvFetchParams {
  offset: number
  limit: number
  filters: FilterRule[]
}

export interface CsvPageResult {
  filename?: string
  header: string[]
  rows: Record<string, string | null>[]
  total_rows: number | null
  filtered_rows: number | null
  offset: number
  limit: number
  has_next: boolean
}

export interface InputFilePreview extends CsvPageResult {
  filename: string
  source?: string
  provider?: string
}

// ─── Admin email test-send ────────────────────────────────────────────────────

export type EmailTestTemplate =
  | 'auth/password_reset'
  | 'auth/email_change_verify'
  | 'notifications/run_complete'

export interface EmailTestRequest {
  to: string
  template: EmailTestTemplate
}

export interface EmailTestSuccess {
  status: 'sent' | 'skipped'
  delivery_id: string
  provider_message_id: string | null
  backend: 'noop' | 'smtp' | 'ses'
}

export interface EmailTestBackendFailure {
  status: 'failed'
  delivery_id: string
  reason: string
  last_error_msg: string | null
  backend: 'noop' | 'smtp' | 'ses'
}

export interface EmailTestPending {
  status: 'pending' | 'sending'
  delivery_id: string
  attempts: number
  reason: string | null
  last_error_msg: string | null
  backend: 'noop' | 'smtp' | 'ses'
}

export interface EmailTestRenderFailure {
  code: string
  message: string
}

export type EmailTestResponse =
  | EmailTestSuccess
  | EmailTestBackendFailure
  | EmailTestPending

// ─── Notification subscriptions (SFBL-182) ───────────────────────────────────

export type NotificationChannel = 'email' | 'webhook'
export type NotificationTrigger = 'terminal_any' | 'terminal_fail_only'

export interface NotificationSubscription {
  id: string
  user_id: string
  plan_id: string | null
  channel: NotificationChannel
  destination: string
  trigger: NotificationTrigger
  created_at: string
  updated_at: string
}

export interface NotificationSubscriptionCreate {
  plan_id?: string | null
  channel: NotificationChannel
  destination: string
  trigger: NotificationTrigger
}

export interface NotificationSubscriptionUpdate {
  plan_id?: string | null
  channel?: NotificationChannel
  destination?: string
  trigger?: NotificationTrigger
}

export interface NotificationTestResponse {
  delivery_id: string
  status: string
  attempts: number
  last_error: string | null
  email_delivery_id: string | null
}

// ─── DB-backed settings (SFBL-157) ───────────────────────────────────────────

export type SettingType = 'str' | 'int' | 'bool' | 'float'

export interface SettingValue {
  key: string
  value: string | number | boolean | null
  type: SettingType
  is_secret: boolean
  description: string
  restart_required: boolean
  updated_at: string | null
}

export interface CategorySettings {
  category: string
  settings: SettingValue[]
}

export interface AllSettings {
  categories: CategorySettings[]
}

/** Free-form key→value patch body sent to PATCH /api/settings/{category} */
export type SettingsPatch = Record<string, string | number | boolean>

// ─── Admin users (SFBL-201) ───────────────────────────────────────────────────

export type AdminUserStatus = 'active' | 'invited' | 'deactivated' | 'locked' | 'deleted'

export interface AdminProfileSummary {
  id: string
  name: string
}

export interface AdminUser {
  id: string
  email: string
  display_name: string | null
  status: AdminUserStatus
  is_admin: boolean
  profile: AdminProfileSummary | null
  permissions: string[]
  invited_by: string | null
  invited_at: string | null
  last_login_at: string | null
}

export interface AdminUserListResponse {
  items: AdminUser[]
  total: number
  page: number
  page_size: number
}

export interface InviteUserRequest {
  email: string
  profile_id: string
  display_name?: string | null
}

export interface InviteUserResponse {
  user: AdminUser
  raw_token: string
  expires_at: string
}

export interface UpdateUserRequest {
  profile_id?: string | null
  display_name?: string | null
}

export interface AdminResetPasswordResponse {
  temp_password: string
  must_reset_password: boolean
}

export interface ResendInviteResponse {
  raw_token: string
  expires_at: string
}

export interface ProfileListItem {
  id: string
  name: string
  description: string | null
}

export interface AdminStatsResponse {
  active_admin_count: number
}

// ─── Dependencies health ──────────────────────────────────────────────────────

export interface DependencyStatus {
  status: 'ok' | 'degraded' | 'failed'
  detail?: string
}

export interface DependenciesResponse {
  status: string
  dependencies: {
    database?: DependencyStatus
    email?: DependencyStatus
    [key: string]: DependencyStatus | undefined
  }
}

// ─── About / system info (SFBL-269) ──────────────────────────────────────────

export interface AboutPayload {
  app: {
    version: string
    git_sha: string
    build_time: string
  }
  distribution: {
    profile: string
    auth_mode: string | null
  }
  runtime: {
    python_version: string
    fastapi_version: string
  }
  database: {
    backend: string
    alembic_head: string
  }
  salesforce: {
    api_version: string
  }
  email: {
    backend: string
    enabled: boolean
  }
  storage: {
    input_connections: Record<string, number>
    output_connections: Record<string, number>
  }
}
