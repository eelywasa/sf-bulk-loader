import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Card, DataTable, EmptyState, Modal, Spinner, type Column } from '../components/ui'
import { useToast } from '../components/ui/Toast'
import { ALERT_ERROR } from '../components/ui/formStyles'
import { plansApi, connectionsApi } from '../api/endpoints'
import PermissionGate from '../components/PermissionGate'
import { usePermission } from '../hooks/usePermission'
import type { LoadPlan } from '../api/types'

import { formatDate } from '../utils/formatters'

// ─── Component ────────────────────────────────────────────────────────────────

export default function PlansPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const toast = useToast()
  const canManage = usePermission('plans.manage')

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

  const duplicateMutation = useMutation({
    mutationFn: (id: string) => plansApi.duplicate(id),
    onSuccess: (newPlan) => {
      queryClient.invalidateQueries({ queryKey: ['plans'] })
      navigate(`/plans/${newPlan.id}`)
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : 'Failed to duplicate plan')
    },
  })

  // ── Table columns ───────────────────────────────────────────────────────────

  const connectionMap = new Map(connections?.map((c) => [c.id, c]) ?? [])

  const columns: Column<LoadPlan>[] = [
    {
      key: 'name',
      header: 'Name',
      render: (p) => <span className="font-medium text-content-primary">{p.name}</span>,
    },
    {
      key: 'connection_id',
      header: 'Connection',
      render: (p) => {
        const conn = connectionMap.get(p.connection_id)
        return (
          <span className="text-sm text-content-secondary">
            {conn ? conn.name : p.connection_id.slice(0, 8) + '…'}
          </span>
        )
      },
    },
    {
      key: 'description',
      header: 'Description',
      render: (p) => <span className="text-sm text-content-muted">{p.description ?? '—'}</span>,
    },
    {
      key: 'created_at',
      header: 'Created',
      render: (p) => <span className="text-sm text-content-muted">{formatDate(p.created_at)}</span>,
    },
    {
      key: 'id',
      header: '',
      className: 'text-right whitespace-nowrap',
      render: (p) => (
        <div className="flex items-center justify-end gap-2">
          {canManage && (
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
          )}
          {canManage && (
            <Button
              size="sm"
              variant="secondary"
              loading={duplicateMutation.isPending && duplicateMutation.variables === p.id}
              disabled={duplicateMutation.isPending}
              onClick={(e) => {
                e.stopPropagation()
                duplicateMutation.mutate(p.id)
              }}
            >
              Duplicate
            </Button>
          )}
          {canManage && (
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
          )}
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
          <h1 className="text-2xl font-bold text-content-primary">Load Plans</h1>
          <p className="mt-1 text-sm text-content-muted">
            Define and manage data load configurations.
          </p>
        </div>
        <PermissionGate permission="plans.manage">
          <Button onClick={() => navigate('/plans/new')}>New Plan</Button>
        </PermissionGate>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex justify-center py-16">
          <Spinner size="md" />
        </div>
      ) : loadError ? (
        <div className={ALERT_ERROR}>
          <p>
            Failed to load plans:{' '}
            {loadError instanceof Error ? loadError.message : 'Unknown error'}
          </p>
        </div>
      ) : !plans?.length ? (
        <EmptyState
          title="No load plans yet"
          description="Create a load plan to define which Salesforce objects to load, in what order, with which CSV files."
          action={
            canManage ? (
              <Button onClick={() => navigate('/plans/new')}>Create Plan</Button>
            ) : undefined
          }
        />
      ) : (
        <Card padding={false}>
          <DataTable
            columns={columns}
            data={plans}
            keyExtractor={(p) => p.id}
            onRowClick={(p) => navigate(`/plans/${p.id}`)}
          />
        </Card>
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
        <p className="text-sm text-content-secondary">
          Are you sure you want to delete{' '}
          <span className="font-semibold">{deleteTarget?.name}</span>? This cannot be undone.
        </p>
      </Modal>
    </div>
  )
}
