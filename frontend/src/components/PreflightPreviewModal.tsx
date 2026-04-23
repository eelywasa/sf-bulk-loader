import { Button, Modal, Badge, Spinner } from './ui'
import type { LoadStep } from '../api/types'
import { operationVariant, type PreviewEntry } from '../pages/planEditorUtils'

interface PreflightPreviewModalProps {
  open: boolean
  steps: LoadStep[]
  previews: Record<string, PreviewEntry>
  onClose: () => void
}

export default function PreflightPreviewModal({
  open,
  steps,
  previews,
  onClose,
}: PreflightPreviewModalProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title="Preflight Check"
      footer={<Button onClick={onClose}>Close</Button>}
    >
      <div className="space-y-4">
        <p className="text-sm text-content-muted">
          Previewing {steps.length} step(s) — checking file patterns and row counts.
        </p>

        {steps.map((step) => {
          const preview = previews[step.id]
          return (
            <div key={step.id} className="rounded border border-border-base p-3">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-xs font-semibold text-content-muted">#{step.sequence}</span>
                <span className="font-medium text-content-primary text-sm">{step.object_name}</span>
                <Badge variant={operationVariant(step.operation)}>{step.operation}</Badge>
              </div>

              {!preview || preview.status === 'idle' ? (
                <span className="text-xs text-content-muted">Not fetched</span>
              ) : preview.status === 'loading' ? (
                <div className="flex items-center gap-2">
                  <Spinner size="xs" aria-label="Loading preview" />
                  <span className="text-xs text-content-muted">Loading…</span>
                </div>
              ) : preview.status === 'error' ? (
                <p className="text-xs text-error-text">{preview.message}</p>
              ) : preview.kind === 'query' ? (
                preview.data.valid ? (
                  <>
                    <p className="text-xs font-semibold text-success-text">SOQL valid</p>
                    {preview.data.plan && (
                      <p className="text-xs text-content-secondary font-mono mt-0.5">
                        {preview.data.plan.sobjectType} • {preview.data.plan.leadingOperation}
                      </p>
                    )}
                  </>
                ) : (
                  <p className="text-xs text-error-text">
                    {preview.data.error || 'SOQL invalid'}
                  </p>
                )
              ) : (
                <>
                  <p className="text-xs font-semibold text-success-text">
                    {preview.data.matched_files.length} file(s) •{' '}
                    {preview.data.total_rows.toLocaleString()} rows
                  </p>
                  {preview.data.matched_files.map((f) => (
                    <p key={f.filename} className="text-xs text-content-secondary font-mono mt-0.5">
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
            </div>
          )
        })}
      </div>
    </Modal>
  )
}
