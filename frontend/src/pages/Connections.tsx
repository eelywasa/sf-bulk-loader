import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { Button, Card, Badge, Modal, DataTable, EmptyState, RequiredAsterisk, Spinner, type Column } from '../components/ui'
import { useToast } from '../components/ui/Toast'
import { LABEL_CLASS, INPUT_CLASS, SELECT_CLASS, TEXTAREA_CLASS, ALERT_ERROR, CHECKBOX_CLASS } from '../components/ui/formStyles'
import { connectionsApi, inputConnectionsApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import PermissionGate from '../components/PermissionGate'
import { usePermission } from '../hooks/usePermission'
import type {
  Connection,
  ConnectionCreate,
  InputConnection,
  InputConnectionCreate,
  ApiValidationError,
} from '../api/types'

import { formatDate } from '../utils/formatters'

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

// ─── Form types ───────────────────────────────────────────────────────────────

interface ConnectionFormData {
  name: string
  username: string
  login_url: string
  instance_url: string
  client_id: string
  private_key: string
  is_sandbox: boolean
}

const EMPTY_FORM: ConnectionFormData = {
  name: '',
  username: '',
  login_url: 'https://login.salesforce.com',
  instance_url: '',
  client_id: '',
  private_key: '',
  is_sandbox: false,
}

const LOGIN_URLS = [
  { value: 'https://login.salesforce.com', label: 'https://login.salesforce.com (Production)' },
  { value: 'https://test.salesforce.com', label: 'https://test.salesforce.com (Sandbox)' },
]

interface InputConnFormData {
  name: string
  bucket: string
  root_prefix: string
  region: string
  access_key_id: string
  secret_access_key: string
  session_token: string
  direction: 'in' | 'out' | 'both'
}

const EMPTY_INPUT_FORM: InputConnFormData = {
  name: '',
  bucket: '',
  root_prefix: '',
  region: '',
  access_key_id: '',
  secret_access_key: '',
  session_token: '',
  direction: 'in',
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function Connections() {
  const queryClient = useQueryClient()
  const toast = useToast()
  const canManage = usePermission('connections.manage')

  // Modal state
  const [modalOpen, setModalOpen] = useState(false)
  const [editingConn, setEditingConn] = useState<Connection | null>(null)
  const [form, setForm] = useState<ConnectionFormData>(EMPTY_FORM)
  const [formErrors, setFormErrors] = useState<string[]>([])

  // Delete confirm
  const [deleteTarget, setDeleteTarget] = useState<Connection | null>(null)

  // ── Input Connection state ──────────────────────────────────────────────────
  const [inputModalOpen, setInputModalOpen] = useState(false)
  const [editingInputConn, setEditingInputConn] = useState<InputConnection | null>(null)
  const [inputForm, setInputForm] = useState<InputConnFormData>(EMPTY_INPUT_FORM)
  const [inputFormErrors, setInputFormErrors] = useState<string[]>([])
  const [inputDeleteTarget, setInputDeleteTarget] = useState<InputConnection | null>(null)
  const [inputTestResult, setInputTestResult] = useState<{
    connectionId: string
    connectionName: string
    success: boolean
    message: string
  } | null>(null)
  const [inputTestingId, setInputTestingId] = useState<string | null>(null)

  // Test result panel
  const [testResult, setTestResult] = useState<{
    connectionId: string
    connectionName: string
    success: boolean
    message: string
    instanceUrl?: string | null
  } | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)

  // ── Queries ────────────────────────────────────────────────────────────────

  const {
    data: connections,
    isLoading,
    error: loadError,
  } = useQuery({
    queryKey: ['connections'],
    queryFn: connectionsApi.list,
  })

  const {
    data: inputConnections,
    isLoading: inputLoading,
    error: inputLoadError,
  } = useQuery({
    queryKey: ['input-connections'],
    queryFn: () => inputConnectionsApi.list(),
  })

  // ── Mutations ──────────────────────────────────────────────────────────────

  const createMutation = useMutation({
    mutationFn: (data: ConnectionCreate) => connectionsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.success('Connection created')
      closeModal()
    },
    onError: (err) => setFormErrors(extractErrors(err)),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<ConnectionCreate> }) =>
      connectionsApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.success('Connection updated')
      closeModal()
    },
    onError: (err) => setFormErrors(extractErrors(err)),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => connectionsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.success('Connection deleted')
      setDeleteTarget(null)
      setTestResult(null)
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : 'Failed to delete connection')
      setDeleteTarget(null)
    },
  })

  const createInputMutation = useMutation({
    mutationFn: (data: InputConnectionCreate) => inputConnectionsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['input-connections'] })
      toast.success('Storage connection created')
      closeInputModal()
    },
    onError: (err) => setInputFormErrors(extractErrors(err)),
  })

  const updateInputMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<InputConnectionCreate> }) =>
      inputConnectionsApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['input-connections'] })
      toast.success('Storage connection updated')
      closeInputModal()
    },
    onError: (err) => setInputFormErrors(extractErrors(err)),
  })

  const deleteInputMutation = useMutation({
    mutationFn: (id: string) => inputConnectionsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['input-connections'] })
      toast.success('Storage connection deleted')
      setInputDeleteTarget(null)
      setInputTestResult(null)
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : 'Failed to delete storage connection')
      setInputDeleteTarget(null)
    },
  })

  // ── Handlers ───────────────────────────────────────────────────────────────

  function openCreate() {
    setEditingConn(null)
    setForm(EMPTY_FORM)
    setFormErrors([])
    setModalOpen(true)
  }

  function openEdit(conn: Connection) {
    setEditingConn(conn)
    setForm({
      name: conn.name,
      username: conn.username,
      login_url: conn.login_url,
      instance_url: conn.instance_url,
      client_id: conn.client_id,
      private_key: '', // never pre-fill the key; leave blank to keep existing
      is_sandbox: conn.is_sandbox,
    })
    setFormErrors([])
    setModalOpen(true)
  }

  function closeModal() {
    setModalOpen(false)
    setEditingConn(null)
    setForm(EMPTY_FORM)
    setFormErrors([])
  }

  function setField<K extends keyof ConnectionFormData>(key: K, value: ConnectionFormData[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  function handleLoginUrlChange(value: string) {
    setField('login_url', value)
    if (value === 'https://test.salesforce.com') {
      setField('is_sandbox', true)
    } else if (value === 'https://login.salesforce.com') {
      setField('is_sandbox', false)
    }
  }

  function handleSubmit() {
    setFormErrors([])

    if (editingConn) {
      const data: Partial<ConnectionCreate> = {
        name: form.name,
        username: form.username,
        login_url: form.login_url,
        instance_url: form.instance_url,
        client_id: form.client_id,
        is_sandbox: form.is_sandbox,
      }
      if (form.private_key.trim()) {
        data.private_key = form.private_key.trim()
      }
      updateMutation.mutate({ id: editingConn.id, data })
    } else {
      createMutation.mutate({
        name: form.name,
        username: form.username,
        login_url: form.login_url,
        instance_url: form.instance_url,
        client_id: form.client_id,
        private_key: form.private_key,
        is_sandbox: form.is_sandbox,
      })
    }
  }

  async function handleTest(conn: Connection) {
    setTestingId(conn.id)
    setTestResult(null)
    try {
      const result = await connectionsApi.test(conn.id)
      setTestResult({
        connectionId: conn.id,
        connectionName: conn.name,
        success: result.success,
        message: result.message,
        instanceUrl: result.instance_url,
      })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Test request failed')
    } finally {
      setTestingId(null)
    }
  }

  function openInputCreate() {
    setEditingInputConn(null)
    setInputForm(EMPTY_INPUT_FORM)
    setInputFormErrors([])
    setInputModalOpen(true)
  }

  function openInputEdit(conn: InputConnection) {
    setEditingInputConn(conn)
    setInputForm({
      name: conn.name,
      bucket: conn.bucket,
      root_prefix: conn.root_prefix ?? '',
      region: conn.region ?? '',
      access_key_id: '',
      secret_access_key: '',
      session_token: '',
      direction: conn.direction,
    })
    setInputFormErrors([])
    setInputModalOpen(true)
  }

  function closeInputModal() {
    setInputModalOpen(false)
    setEditingInputConn(null)
    setInputForm(EMPTY_INPUT_FORM)
    setInputFormErrors([])
  }

  function setInputField<K extends keyof InputConnFormData>(key: K, value: InputConnFormData[K]) {
    setInputForm((prev) => ({ ...prev, [key]: value }))
  }

  function handleInputSubmit() {
    setInputFormErrors([])

    if (editingInputConn) {
      const data: Partial<InputConnectionCreate> = {
        name: inputForm.name,
        bucket: inputForm.bucket,
        root_prefix: inputForm.root_prefix || null,
        region: inputForm.region || null,
        direction: inputForm.direction,
      }
      if (inputForm.access_key_id.trim()) data.access_key_id = inputForm.access_key_id.trim()
      if (inputForm.secret_access_key.trim()) data.secret_access_key = inputForm.secret_access_key.trim()
      if (inputForm.session_token.trim()) data.session_token = inputForm.session_token.trim()
      updateInputMutation.mutate({ id: editingInputConn.id, data })
    } else {
      createInputMutation.mutate({
        name: inputForm.name,
        provider: 's3',
        bucket: inputForm.bucket,
        root_prefix: inputForm.root_prefix || null,
        region: inputForm.region || null,
        access_key_id: inputForm.access_key_id,
        secret_access_key: inputForm.secret_access_key,
        session_token: inputForm.session_token || null,
        direction: inputForm.direction,
      })
    }
  }

  async function handleInputTest(conn: InputConnection) {
    setInputTestingId(conn.id)
    setInputTestResult(null)
    try {
      const result = await inputConnectionsApi.test(conn.id)
      setInputTestResult({
        connectionId: conn.id,
        connectionName: conn.name,
        success: result.success,
        message: result.message,
      })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Test request failed')
    } finally {
      setInputTestingId(null)
    }
  }

  const isSaving = createMutation.isPending || updateMutation.isPending
  const isInputSaving = createInputMutation.isPending || updateInputMutation.isPending

  // ── Table columns ──────────────────────────────────────────────────────────

  const columns: Column<Connection>[] = [
    {
      key: 'name',
      header: 'Name',
      render: (c) => <span className="font-medium text-content-primary">{c.name}</span>,
    },
    { key: 'username', header: 'Username', render: (c) => <span className="break-all">{c.username}</span> },
    {
      key: 'instance_url',
      header: 'Instance URL',
      render: (c) => (
        <span className="font-mono text-xs text-content-secondary break-all">{c.instance_url}</span>
      ),
    },
    {
      key: 'is_sandbox',
      header: 'Type',
      render: (c) => (
        <Badge variant={c.is_sandbox ? 'warning' : 'info'}>
          {c.is_sandbox ? 'Sandbox' : 'Production'}
        </Badge>
      ),
    },
    {
      key: 'created_at',
      header: 'Created',
      render: (c) => <span className="text-content-muted text-sm">{formatDate(c.created_at)}</span>,
    },
    {
      key: 'id',
      header: '',
      className: 'text-right whitespace-nowrap',
      render: (c) => (
        <div className="flex items-center justify-end gap-2">
          <Button
            size="sm"
            variant="ghost"
            loading={testingId === c.id}
            disabled={testingId !== null}
            onClick={(e) => {
              e.stopPropagation()
              void handleTest(c)
            }}
          >
            Test
          </Button>
          {canManage && (
            <>
              <Button
                size="sm"
                variant="secondary"
                onClick={(e) => {
                  e.stopPropagation()
                  openEdit(c)
                }}
              >
                Edit
              </Button>
              <Button
                size="sm"
                variant="danger"
                onClick={(e) => {
                  e.stopPropagation()
                  setDeleteTarget(c)
                }}
              >
                Delete
              </Button>
            </>
          )}
        </div>
      ),
    },
  ]

  const inputColumns: Column<InputConnection>[] = [
    {
      key: 'name',
      header: 'Name',
      render: (c) => <span className="font-medium text-content-primary">{c.name}</span>,
    },
    { key: 'bucket', header: 'Bucket', render: (c) => <span className="font-mono text-xs text-content-secondary">{c.bucket}</span> },
    {
      key: 'region',
      header: 'Region',
      render: (c) => <span className="text-content-secondary text-sm">{c.region ?? '—'}</span>,
    },
    {
      key: 'root_prefix',
      header: 'Root Prefix',
      render: (c) => (
        <span className="font-mono text-xs text-content-muted">{c.root_prefix ?? '—'}</span>
      ),
    },
    {
      key: 'direction',
      header: 'Direction',
      render: (c) => (
        <Badge variant={c.direction === 'in' ? 'info' : c.direction === 'out' ? 'warning' : 'success'}>
          {c.direction === 'in' ? 'Input' : c.direction === 'out' ? 'Output' : 'Both'}
        </Badge>
      ),
    },
    {
      key: 'created_at',
      header: 'Created',
      render: (c) => <span className="text-content-muted text-sm">{formatDate(c.created_at)}</span>,
    },
    {
      key: 'id',
      header: '',
      className: 'text-right whitespace-nowrap',
      render: (c) => (
        <div className="flex items-center justify-end gap-2">
          <Button
            size="sm"
            variant="ghost"
            loading={inputTestingId === c.id}
            disabled={inputTestingId !== null}
            onClick={(e) => {
              e.stopPropagation()
              void handleInputTest(c)
            }}
          >
            Test
          </Button>
          {canManage && (
            <>
              <Button
                size="sm"
                variant="secondary"
                onClick={(e) => {
                  e.stopPropagation()
                  openInputEdit(c)
                }}
              >
                Edit
              </Button>
              <Button
                size="sm"
                variant="danger"
                onClick={(e) => {
                  e.stopPropagation()
                  setInputDeleteTarget(c)
                }}
              >
                Delete
              </Button>
            </>
          )}
        </div>
      ),
    },
  ]

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-content-primary">Connections</h1>
          <p className="mt-1 text-sm text-content-muted">
            Manage Salesforce org connections and S3 input sources.
          </p>
        </div>
      </div>

      {/* ── Salesforce Connections ─────────────────────────────────────────── */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-content-primary">
              Salesforce Connections
            </h2>
            <p className="text-sm text-content-muted">
              JWT Bearer auth — one entry per org.
            </p>
          </div>
          <PermissionGate permission="connections.manage">
            <Button onClick={openCreate}>New Salesforce Connection</Button>
          </PermissionGate>
        </div>

        {/* SF test result panel */}
        {testResult && (
          <div
            role="status"
            aria-live="polite"
            className={clsx(
              'rounded-lg border p-4',
              testResult.success ? 'bg-success-bg border-success-border' : 'bg-error-bg border-error-border',
            )}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-1 min-w-0">
                <p
                  className={clsx(
                    'font-medium',
                    testResult.success ? 'text-success-text' : 'text-error-text',
                  )}
                >
                  {testResult.success ? '✓ Connection successful' : '✗ Connection failed'}
                  {' — '}
                  <span className="font-normal">{testResult.connectionName}</span>
                </p>
                <p
                  className={clsx(
                    'text-sm',
                    testResult.success ? 'text-success-text' : 'text-error-text',
                  )}
                >
                  {testResult.message}
                </p>
                {testResult.instanceUrl && (
                  <p className="text-xs font-mono text-content-secondary break-all">
                    {testResult.instanceUrl}
                  </p>
                )}
              </div>
              <button
                type="button"
                aria-label="Dismiss test result"
                onClick={() => setTestResult(null)}
                className="shrink-0 text-content-muted hover:text-content-secondary text-xl leading-none"
              >
                ×
              </button>
            </div>
          </div>
        )}

        {/* SF content area */}
        {isLoading ? (
          <div className="flex justify-center py-16">
            <Spinner size="md" />
          </div>
        ) : loadError ? (
          <Card>
            <p className="text-error-text text-sm">
              Failed to load connections:{' '}
              {loadError instanceof Error ? loadError.message : 'Unknown error'}
            </p>
          </Card>
        ) : !connections?.length ? (
          <EmptyState
            title="No connections yet"
            description="Add a Salesforce connection to get started. You'll need a Connected App configured for JWT Bearer authentication."
          />
        ) : (
          <DataTable columns={columns} data={connections} keyExtractor={(c) => c.id} />
        )}
      </div>

      {/* ── Storage Connections ────────────────────────────────────────────── */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-content-primary">
              Storage Connections
            </h2>
            <p className="text-sm text-content-muted">
              Remote S3 buckets used as CSV input sources or output destinations for load steps.
            </p>
          </div>
          <PermissionGate permission="connections.manage">
            <Button onClick={openInputCreate}>New Storage Connection</Button>
          </PermissionGate>
        </div>

        {/* Input test result panel */}
        {inputTestResult && (
          <div
            role="status"
            aria-live="polite"
            className={clsx(
              'rounded-lg border p-4',
              inputTestResult.success
                ? 'bg-success-bg border-success-border'
                : 'bg-error-bg border-error-border',
            )}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-1 min-w-0">
                <p
                  className={clsx(
                    'font-medium',
                    inputTestResult.success ? 'text-success-text' : 'text-error-text',
                  )}
                >
                  {inputTestResult.success ? '✓ Connection successful' : '✗ Connection failed'}
                  {' — '}
                  <span className="font-normal">{inputTestResult.connectionName}</span>
                </p>
                <p
                  className={clsx(
                    'text-sm',
                    inputTestResult.success ? 'text-success-text' : 'text-error-text',
                  )}
                >
                  {inputTestResult.message}
                </p>
              </div>
              <button
                type="button"
                aria-label="Dismiss input test result"
                onClick={() => setInputTestResult(null)}
                className="shrink-0 text-content-muted hover:text-content-secondary text-xl leading-none"
              >
                ×
              </button>
            </div>
          </div>
        )}

        {/* Input content area */}
        {inputLoading ? (
          <div className="flex justify-center py-16">
            <Spinner size="md" aria-label="Loading input connections" />
          </div>
        ) : inputLoadError ? (
          <Card>
            <p className="text-error-text text-sm">
              Failed to load storage connections:{' '}
              {inputLoadError instanceof Error ? inputLoadError.message : 'Unknown error'}
            </p>
          </Card>
        ) : !inputConnections?.length ? (
          <EmptyState
            title="No storage connections yet"
            description="Add an S3 connection to use remote CSV files as input sources or output destinations for load steps."
          />
        ) : (
          <DataTable columns={inputColumns} data={inputConnections} keyExtractor={(c) => c.id} />
        )}
      </div>

      {/* ── Create / Edit Modal ─────────────────────────────────────────────── */}
      <Modal
        open={modalOpen}
        onClose={closeModal}
        closeOnBackdropClick={false}
        size="lg"
        title={editingConn ? 'Edit Connection' : 'New Connection'}
        footer={
          <>
            <Button variant="secondary" onClick={closeModal} disabled={isSaving}>
              Cancel
            </Button>
            <Button
              loading={isSaving}
              onClick={handleSubmit}
            >
              {editingConn ? 'Save Changes' : 'Create Connection'}
            </Button>
          </>
        }
      >
        <form
          id="connection-form"
          onSubmit={(e) => {
            e.preventDefault()
            handleSubmit()
          }}
          className="space-y-4"
          noValidate
        >
          {/* Validation error summary */}
          {formErrors.length > 0 && (
            <div role="alert" className={`${ALERT_ERROR} space-y-1`}>
              {formErrors.map((msg, i) => (
                <p key={i}>{msg}</p>
              ))}
            </div>
          )}

          {/* Name */}
          <div>
            <label htmlFor="conn-name" className={LABEL_CLASS}>
              Name <RequiredAsterisk />
            </label>
            <input
              id="conn-name"
              type="text"
              required
              value={form.name}
              onChange={(e) => setField('name', e.target.value)}
              placeholder="Production"
              className={INPUT_CLASS}
            />
          </div>

          {/* Username */}
          <div>
            <label htmlFor="conn-username" className={LABEL_CLASS}>
              Username <RequiredAsterisk />
            </label>
            <input
              id="conn-username"
              type="text"
              required
              value={form.username}
              onChange={(e) => setField('username', e.target.value)}
              placeholder="user@example.com"
              className={INPUT_CLASS}
            />
          </div>

          {/* Login URL */}
          <div>
            <label htmlFor="conn-login-url" className={LABEL_CLASS}>
              Login URL <RequiredAsterisk />
            </label>
            <select
              id="conn-login-url"
              value={LOGIN_URLS.some((u) => u.value === form.login_url) ? form.login_url : ''}
              onChange={(e) => handleLoginUrlChange(e.target.value)}
              className={SELECT_CLASS}
            >
              {LOGIN_URLS.map((u) => (
                <option key={u.value} value={u.value}>
                  {u.label}
                </option>
              ))}
              {!LOGIN_URLS.some((u) => u.value === form.login_url) && (
                <option value={form.login_url}>{form.login_url}</option>
              )}
            </select>
          </div>

          {/* Instance URL */}
          <div>
            <label htmlFor="conn-instance-url" className={LABEL_CLASS}>
              Instance URL <RequiredAsterisk />
            </label>
            <input
              id="conn-instance-url"
              type="url"
              required
              value={form.instance_url}
              onChange={(e) => setField('instance_url', e.target.value)}
              placeholder="https://myorg.my.salesforce.com"
              className={INPUT_CLASS}
            />
          </div>

          {/* Consumer Key */}
          <div>
            <label htmlFor="conn-client-id" className={LABEL_CLASS}>
              Consumer Key (Client ID) <RequiredAsterisk />
            </label>
            <input
              id="conn-client-id"
              type="text"
              required
              value={form.client_id}
              onChange={(e) => setField('client_id', e.target.value)}
              placeholder="3MVG9..."
              className={clsx(INPUT_CLASS, 'font-mono')}
            />
          </div>

          {/* Private Key */}
          <div>
            <label htmlFor="conn-private-key" className={LABEL_CLASS}>
              Private Key (PEM)
              {!editingConn && <RequiredAsterisk />}
            </label>
            <textarea
              id="conn-private-key"
              rows={5}
              required={!editingConn}
              value={form.private_key}
              onChange={(e) => setField('private_key', e.target.value)}
              placeholder={
                editingConn
                  ? 'Leave blank to keep the existing private key'
                  : '-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----'
              }
              className={clsx(TEXTAREA_CLASS, 'font-mono')}
            />
          </div>

          {/* Sandbox toggle */}
          <div className="flex items-center gap-3">
            <input
              id="conn-sandbox"
              type="checkbox"
              checked={form.is_sandbox}
              onChange={(e) => setField('is_sandbox', e.target.checked)}
              className={CHECKBOX_CLASS}
            />
            <label htmlFor="conn-sandbox" className="text-sm font-medium text-content-secondary">
              Sandbox org
            </label>
          </div>
        </form>
      </Modal>

      {/* ── Delete Confirmation Modal ──────────────────────────────────────── */}
      <Modal
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        size="sm"
        title="Delete Connection"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setDeleteTarget(null)}
              disabled={deleteMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={deleteMutation.isPending}
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-content-secondary">
          Are you sure you want to delete{' '}
          <span className="font-semibold">{deleteTarget?.name}</span>? This cannot be undone.
        </p>
      </Modal>

      {/* ── Input Connection Create / Edit Modal ───────────────────────────── */}
      <Modal
        open={inputModalOpen}
        onClose={closeInputModal}
        closeOnBackdropClick={false}
        size="lg"
        title={editingInputConn ? 'Edit Storage Connection' : 'New Storage Connection'}
        footer={
          <>
            <Button variant="secondary" onClick={closeInputModal} disabled={isInputSaving}>
              Cancel
            </Button>
            <Button loading={isInputSaving} onClick={handleInputSubmit}>
              {editingInputConn ? 'Save Changes' : 'Create Storage Connection'}
            </Button>
          </>
        }
      >
        <form
          id="input-connection-form"
          onSubmit={(e) => {
            e.preventDefault()
            handleInputSubmit()
          }}
          className="space-y-4"
          noValidate
        >
          {inputFormErrors.length > 0 && (
            <div role="alert" className={`${ALERT_ERROR} space-y-1`}>
              {inputFormErrors.map((msg, i) => (
                <p key={i}>{msg}</p>
              ))}
            </div>
          )}

          <div>
            <label htmlFor="ic-name" className={LABEL_CLASS}>
              Name <RequiredAsterisk />
            </label>
            <input
              id="ic-name"
              type="text"
              required
              value={inputForm.name}
              onChange={(e) => setInputField('name', e.target.value)}
              placeholder="My S3 Bucket"
              className={INPUT_CLASS}
            />
          </div>

          <div>
            <label htmlFor="ic-direction" className={LABEL_CLASS}>
              Direction
            </label>
            <select
              id="ic-direction"
              value={inputForm.direction}
              onChange={(e) => setInputField('direction', e.target.value as 'in' | 'out' | 'both')}
              className={SELECT_CLASS}
            >
              <option value="in">Input only</option>
              <option value="out">Output only</option>
              <option value="both">Input &amp; Output</option>
            </select>
          </div>

          <div>
            <label htmlFor="ic-bucket" className={LABEL_CLASS}>
              Bucket <RequiredAsterisk />
            </label>
            <input
              id="ic-bucket"
              type="text"
              required
              value={inputForm.bucket}
              onChange={(e) => setInputField('bucket', e.target.value)}
              placeholder="my-data-bucket"
              className={clsx(INPUT_CLASS, 'font-mono')}
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="ic-region" className={LABEL_CLASS}>
                Region
              </label>
              <input
                id="ic-region"
                type="text"
                value={inputForm.region}
                onChange={(e) => setInputField('region', e.target.value)}
                placeholder="us-east-1"
                className={clsx(INPUT_CLASS, 'font-mono')}
              />
            </div>
            <div>
              <label htmlFor="ic-root-prefix" className={LABEL_CLASS}>
                Root Prefix
              </label>
              <input
                id="ic-root-prefix"
                type="text"
                value={inputForm.root_prefix}
                onChange={(e) => setInputField('root_prefix', e.target.value)}
                placeholder="data/csvs/"
                className={clsx(INPUT_CLASS, 'font-mono')}
              />
            </div>
          </div>

          <div>
            <label htmlFor="ic-access-key-id" className={LABEL_CLASS}>
              Access Key ID
              {!editingInputConn && <RequiredAsterisk />}
            </label>
            <input
              id="ic-access-key-id"
              type="text"
              required={!editingInputConn}
              value={inputForm.access_key_id}
              onChange={(e) => setInputField('access_key_id', e.target.value)}
              placeholder={editingInputConn ? 'Leave blank to keep existing' : 'AKIA...'}
              className={clsx(INPUT_CLASS, 'font-mono')}
            />
          </div>

          <div>
            <label htmlFor="ic-secret-access-key" className={LABEL_CLASS}>
              Secret Access Key
              {!editingInputConn && <RequiredAsterisk />}
            </label>
            <input
              id="ic-secret-access-key"
              type="password"
              required={!editingInputConn}
              value={inputForm.secret_access_key}
              onChange={(e) => setInputField('secret_access_key', e.target.value)}
              placeholder={editingInputConn ? 'Leave blank to keep existing' : '••••••••'}
              className={clsx(INPUT_CLASS, 'font-mono')}
            />
          </div>

          <div>
            <label htmlFor="ic-session-token" className={LABEL_CLASS}>
              Session Token
            </label>
            <input
              id="ic-session-token"
              type="password"
              value={inputForm.session_token}
              onChange={(e) => setInputField('session_token', e.target.value)}
              placeholder={
                editingInputConn ? 'Leave blank to keep existing (or clear it)' : 'Optional'
              }
              className={clsx(INPUT_CLASS, 'font-mono')}
            />
          </div>
        </form>
      </Modal>

      {/* ── Input Connection Delete Confirmation ───────────────────────────── */}
      <Modal
        open={inputDeleteTarget !== null}
        onClose={() => setInputDeleteTarget(null)}
        size="sm"
        title="Delete Storage Connection"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setInputDeleteTarget(null)}
              disabled={deleteInputMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={deleteInputMutation.isPending}
              onClick={() =>
                inputDeleteTarget && deleteInputMutation.mutate(inputDeleteTarget.id)
              }
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-content-secondary">
          Are you sure you want to delete{' '}
          <span className="font-semibold">{inputDeleteTarget?.name}</span>? This cannot be undone.
        </p>
      </Modal>
    </div>
  )
}
