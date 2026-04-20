// ─── Runtime config ────────────────────────────────────────────────────────────

export interface RuntimeConfig {
  auth_mode: 'none' | 'local'
  app_distribution: string
  transport_mode: string
  input_storage_mode: string
}

// ─── Auth ──────────────────────────────────────────────────────────────────────

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
}

export interface UserResponse {
  id: string
  username: string | null
  email: string | null
  display_name: string | null
  role: string
  is_active: boolean
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
