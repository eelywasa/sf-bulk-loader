import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, DataTable, EmptyState, Modal, type Column } from '../components/ui'
import { useToast } from '../components/ui/Toast'
import { plansApi, connectionsApi } from '../api/endpoints'
import type { LoadPlan } from '../api/types'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function PlansPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const toast = useToast()

  const [deleteTarget, setDeleteTarget] = useState<LoadPlan | null>(null)

  // ── Queries ─────────────────────────────────────────────────────────────────

  const {
    data: plans,
    isLoading,
    error: loadError,
  } = useQuery({
    queryKey: ['plans'],
    queryFn: plansApi.list,
  })

  const { data: connections } = useQuery({
    queryKey: ['connections'],
    queryFn: connectionsApi.list,
  })

  // ── Mutations ───────────────────────────────────────────────────────────────

  const deleteMutation = useMutation({
    mutationFn: (id: string) => plansApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['plans'] })
      toast.success('Plan deleted')
      setDeleteTarget(null)
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : 'Failed to delete plan')
      setDeleteTarget(null)
    },
  })

  // ── Table columns ───────────────────────────────────────────────────────────

  const connectionMap = new Map(connections?.map((c) => [c.id, c]) ?? [])

  const columns: Column<LoadPlan>[] = [
    {
      key: 'name',
      header: 'Name',
      render: (p) => <span className="font-medium text-gray-900">{p.name}</span>,
    },
    {
      key: 'connection_id',
      header: 'Connection',
      render: (p) => {
        const conn = connectionMap.get(p.connection_id)
        return (
          <span className="text-sm text-gray-600">
            {conn ? conn.name : p.connection_id.slice(0, 8) + '…'}
          </span>
        )
      },
    },
    {
      key: 'description',
      header: 'Description',
      render: (p) => <span className="text-sm text-gray-500">{p.description ?? '—'}</span>,
    },
    {
      key: 'created_at',
      header: 'Created',
      render: (p) => <span className="text-sm text-gray-500">{formatDate(p.created_at)}</span>,
    },
    {
      key: 'id',
      header: '',
      className: 'text-right whitespace-nowrap',
      render: (p) => (
        <div className="flex items-center justify-end gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={(e) => {
              e.stopPropagation()
              navigate(`/plans/${p.id}`)
            }}
          >
            Edit
          </Button>
          <Button
            size="sm"
            variant="danger"
            onClick={(e) => {
              e.stopPropagation()
              setDeleteTarget(p)
            }}
          >
            Delete
          </Button>
        </div>
      ),
    },
  ]

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Load Plans</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Define and manage data load configurations.
          </p>
        </div>
        <Button onClick={() => navigate('/plans/new')}>New Plan</Button>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex justify-center py-16">
          <span
            aria-label="Loading"
            className="h-7 w-7 rounded-full border-2 border-blue-600 border-t-transparent animate-spin"
          />
        </div>
      ) : loadError ? (
        <div className="rounded border border-red-200 bg-red-50 p-4">
          <p className="text-red-700 text-sm">
            Failed to load plans:{' '}
            {loadError instanceof Error ? loadError.message : 'Unknown error'}
          </p>
        </div>
      ) : !plans?.length ? (
        <EmptyState
          title="No load plans yet"
          description="Create a load plan to define which Salesforce objects to load, in what order, with which CSV files."
          action={<Button onClick={() => navigate('/plans/new')}>Create Plan</Button>}
        />
      ) : (
        <DataTable
          columns={columns}
          data={plans}
          keyExtractor={(p) => p.id}
          onRowClick={(p) => navigate(`/plans/${p.id}`)}
        />
      )}

      {/* Delete confirmation modal */}
      <Modal
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        size="sm"
        title="Delete Plan"
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
