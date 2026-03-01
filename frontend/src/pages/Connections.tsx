import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { Button, Card, Badge, Modal, DataTable, EmptyState, type Column } from '../components/ui'
import { useToast } from '../components/ui/Toast'
import { connectionsApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import type { Connection, ConnectionCreate, ApiValidationError } from '../api/types'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

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

// ─── Component ────────────────────────────────────────────────────────────────

export default function Connections() {
  const queryClient = useQueryClient()
  const toast = useToast()

  // Modal state
  const [modalOpen, setModalOpen] = useState(false)
  const [editingConn, setEditingConn] = useState<Connection | null>(null)
  const [form, setForm] = useState<ConnectionFormData>(EMPTY_FORM)
  const [formErrors, setFormErrors] = useState<string[]>([])

  // Delete confirm
  const [deleteTarget, setDeleteTarget] = useState<Connection | null>(null)

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

  const isSaving = createMutation.isPending || updateMutation.isPending

  // ── Table columns ──────────────────────────────────────────────────────────

  const columns: Column<Connection>[] = [
    {
      key: 'name',
      header: 'Name',
      render: (c) => <span className="font-medium text-gray-900">{c.name}</span>,
    },
    { key: 'username', header: 'Username', render: (c) => <span>{c.username}</span> },
    {
      key: 'instance_url',
      header: 'Instance URL',
      render: (c) => (
        <span className="font-mono text-xs text-gray-600 break-all">{c.instance_url}</span>
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
      render: (c) => <span className="text-gray-500 text-sm">{formatDate(c.created_at)}</span>,
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
          <h1 className="text-2xl font-bold text-gray-900">Connections</h1>
          <p className="mt-1 text-sm text-gray-500">
            Manage Salesforce org connections (JWT Bearer auth).
          </p>
        </div>
        <Button onClick={openCreate}>New Connection</Button>
      </div>

      {/* Test result panel */}
      {testResult && (
        <div
          role="status"
          aria-live="polite"
          className={clsx(
            'rounded-lg border p-4',
            testResult.success
              ? 'bg-green-50 border-green-200'
              : 'bg-red-50 border-red-200',
          )}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-1 min-w-0">
              <p
                className={clsx(
                  'font-medium',
                  testResult.success ? 'text-green-800' : 'text-red-800',
                )}
              >
                {testResult.success ? '✓ Connection successful' : '✗ Connection failed'}
                {' — '}
                <span className="font-normal">{testResult.connectionName}</span>
              </p>
              <p
                className={clsx(
                  'text-sm',
                  testResult.success ? 'text-green-700' : 'text-red-700',
                )}
              >
                {testResult.message}
              </p>
              {testResult.instanceUrl && (
                <p className="text-xs font-mono text-gray-600 break-all">
                  {testResult.instanceUrl}
                </p>
              )}
            </div>
            <button
              type="button"
              aria-label="Dismiss test result"
              onClick={() => setTestResult(null)}
              className="shrink-0 text-gray-400 hover:text-gray-600 text-xl leading-none"
            >
              ×
            </button>
          </div>
        </div>
      )}

      {/* Content area */}
      {isLoading ? (
        <div className="flex justify-center py-16">
          <span
            aria-label="Loading"
            className="h-7 w-7 rounded-full border-2 border-blue-600 border-t-transparent animate-spin"
          />
        </div>
      ) : loadError ? (
        <Card>
          <p className="text-red-700 text-sm">
            Failed to load connections:{' '}
            {loadError instanceof Error ? loadError.message : 'Unknown error'}
          </p>
        </Card>
      ) : !connections?.length ? (
        <EmptyState
          title="No connections yet"
          description="Add a Salesforce connection to get started. You'll need a Connected App configured for JWT Bearer authentication."
          action={<Button onClick={openCreate}>Add Connection</Button>}
        />
      ) : (
        <DataTable columns={columns} data={connections} keyExtractor={(c) => c.id} />
      )}

      {/* ── Create / Edit Modal ─────────────────────────────────────────────── */}
      <Modal
        open={modalOpen}
        onClose={closeModal}
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
            <div
              role="alert"
              className="rounded border border-red-200 bg-red-50 p-3 space-y-1"
            >
              {formErrors.map((msg, i) => (
                <p key={i} className="text-sm text-red-700">
                  {msg}
                </p>
              ))}
            </div>
          )}

          {/* Name */}
          <div>
            <label htmlFor="conn-name" className="block text-sm font-medium text-gray-700 mb-1">
              Name <span className="text-red-500">*</span>
            </label>
            <input
              id="conn-name"
              type="text"
              required
              value={form.name}
              onChange={(e) => setField('name', e.target.value)}
              placeholder="Production"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Username */}
          <div>
            <label
              htmlFor="conn-username"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Username <span className="text-red-500">*</span>
            </label>
            <input
              id="conn-username"
              type="text"
              required
              value={form.username}
              onChange={(e) => setField('username', e.target.value)}
              placeholder="user@example.com"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Login URL */}
          <div>
            <label
              htmlFor="conn-login-url"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Login URL <span className="text-red-500">*</span>
            </label>
            <select
              id="conn-login-url"
              value={LOGIN_URLS.some((u) => u.value === form.login_url) ? form.login_url : ''}
              onChange={(e) => handleLoginUrlChange(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
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
            <label
              htmlFor="conn-instance-url"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Instance URL <span className="text-red-500">*</span>
            </label>
            <input
              id="conn-instance-url"
              type="url"
              required
              value={form.instance_url}
              onChange={(e) => setField('instance_url', e.target.value)}
              placeholder="https://myorg.my.salesforce.com"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Consumer Key */}
          <div>
            <label
              htmlFor="conn-client-id"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Consumer Key (Client ID) <span className="text-red-500">*</span>
            </label>
            <input
              id="conn-client-id"
              type="text"
              required
              value={form.client_id}
              onChange={(e) => setField('client_id', e.target.value)}
              placeholder="3MVG9..."
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Private Key */}
          <div>
            <label
              htmlFor="conn-private-key"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Private Key (PEM)
              {!editingConn && <span className="text-red-500"> *</span>}
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
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Sandbox toggle */}
          <div className="flex items-center gap-3">
            <input
              id="conn-sandbox"
              type="checkbox"
              checked={form.is_sandbox}
              onChange={(e) => setField('is_sandbox', e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
            />
            <label htmlFor="conn-sandbox" className="text-sm font-medium text-gray-700">
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
        <p className="text-sm text-gray-700">
          Are you sure you want to delete{' '}
          <span className="font-semibold">{deleteTarget?.name}</span>? This cannot be undone.
        </p>
      </Modal>
    </div>
  )
}
