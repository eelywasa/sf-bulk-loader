import clsx from 'clsx'
import { Button, Card, Badge } from './ui'
import type { LoadStep, InputConnection } from '../api/types'
import { operationVariant, isQueryOp, type PreviewEntry } from '../pages/planEditorUtils'

interface StepListProps {
  steps: LoadStep[]
  previews: Record<string, PreviewEntry>
  inputConnections: InputConnection[]
  reorderPending: boolean
  onEdit: (step: LoadStep) => void
  onDelete: (step: LoadStep) => void
  onMoveUp: (step: LoadStep) => void
  onMoveDown: (step: LoadStep) => void
  onPreview: (step: LoadStep) => void
  onAddStep: () => void
}

export default function StepList({
  steps,
  previews,
  inputConnections,
  reorderPending,
  onEdit,
  onDelete,
  onMoveUp,
  onMoveDown,
  onPreview,
  onAddStep,
}: StepListProps) {
  return (
    <Card
      title="Load Steps"
      actions={
        <Button size="sm" onClick={onAddStep}>
          Add Step
        </Button>
      }
    >
      {steps.length === 0 ? (
        <div className="py-8 text-center">
          <p className="text-sm text-content-muted">
            No steps yet. Add a step to define what data to load.
          </p>
          <div className="mt-3">
            <Button size="sm" onClick={onAddStep}>
              Add Step
            </Button>
          </div>
        </div>
      ) : (
        <div className="divide-y divide-border-base">
          {steps.map((step, idx) => {
            const preview = previews[step.id]
            return (
              <div key={step.id} className="py-4">
                {/* Step row */}
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3 min-w-0">
                    <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-info-bg text-xs font-semibold text-info-text">
                      {step.sequence}
                    </span>
                    <div className="min-w-0">
                      <p className="font-medium text-content-primary text-sm">
                        {step.object_name}
                        <span className="ml-2">
                          <Badge variant={operationVariant(step.operation)}>
                            {step.operation}
                          </Badge>
                        </span>
                      </p>
                      {isQueryOp(step.operation) ? (
                        <p className="text-xs text-content-muted mt-0.5 font-mono truncate">
                          {step.soql ? step.soql.substring(0, 80) + (step.soql.length > 80 ? '…' : '') : '(no SOQL)'}
                        </p>
                      ) : (
                        <p className="text-xs text-content-muted mt-0.5 font-mono truncate">
                          <span className="not-italic font-sans text-content-muted mr-1">
                            {step.input_connection_id
                              ? (inputConnections.find((c) => c.id === step.input_connection_id)?.name ?? 'S3')
                              : 'Local'}
                            {' · '}
                          </span>
                          {step.csv_file_pattern}
                        </p>
                      )}
                      {step.external_id_field && (
                        <p className="text-xs text-content-muted mt-0.5">
                          External ID: {step.external_id_field}
                        </p>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-1 shrink-0">
                    {!isQueryOp(step.operation) && (
                      <Button
                        size="sm"
                        variant="ghost"
                        loading={preview?.status === 'loading'}
                        onClick={() => onPreview(step)}
                      >
                        Preview
                      </Button>
                    )}
                    <Button size="sm" variant="secondary" onClick={() => onEdit(step)}>
                      Edit
                    </Button>
                    <Button size="sm" variant="danger" onClick={() => onDelete(step)}>
                      Delete
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={idx === 0 || reorderPending}
                      onClick={() => onMoveUp(step)}
                      aria-label="Move step up"
                    >
                      ↑
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={idx === steps.length - 1 || reorderPending}
                      onClick={() => onMoveDown(step)}
                      aria-label="Move step down"
                    >
                      ↓
                    </Button>
                  </div>
                </div>

                {/* Inline preview result */}
                {preview && preview.status !== 'idle' && (
                  <div
                    className={clsx(
                      'mt-3 ml-9 rounded-md p-3 text-sm',
                      preview.status === 'loading' && 'bg-surface-sunken',
                      preview.status === 'success' && 'bg-info-bg',
                      preview.status === 'error' && 'bg-error-bg',
                    )}
                  >
                    {preview.status === 'loading' && (
                      <span className="text-content-muted">Loading preview…</span>
                    )}
                    {preview.status === 'error' && (
                      <span className="text-error-text">{preview.message}</span>
                    )}
                    {preview.status === 'success' && preview.kind === 'dml' && (
                      <>
                        <p className="font-medium text-info-text">
                          {preview.data.matched_files.length} file(s) matched •{' '}
                          {preview.data.total_rows.toLocaleString()} total rows
                        </p>
                        {preview.data.matched_files.map((f) => (
                          <p key={f.filename} className="text-xs text-info-text mt-1 font-mono">
                            {f.filename} — {f.row_count.toLocaleString()} rows
                          </p>
                        ))}
                        {preview.data.matched_files.length === 0 && (
                          <p className="text-xs text-warning-text mt-1">
                            No files matched "{step.csv_file_pattern}"
                          </p>
                        )}
                      </>
                    )}
                    {preview.status === 'success' && preview.kind === 'query' && (
                      preview.data.valid ? (
                        <p className="font-medium text-info-text">
                          SOQL valid
                          {preview.data.plan && (
                            <span className="ml-2 text-xs font-mono">
                              ({preview.data.plan.sobjectType} • {preview.data.plan.leadingOperation})
                            </span>
                          )}
                        </p>
                      ) : (
                        <p className="text-error-text text-xs">
                          {preview.data.error || 'SOQL invalid'}
                        </p>
                      )
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}
