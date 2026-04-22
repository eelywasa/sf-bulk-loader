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
}

export interface UserProfile {
  name: 'admin' | 'operator' | 'viewer' | 'desktop' | string
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
  external_id_field?: string | null
  csv_file_pattern?: string | null
  soql?: string | null
  partition_size: number
  assignment_rule_id?: string | null
  input_connection_id?: string | null
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
