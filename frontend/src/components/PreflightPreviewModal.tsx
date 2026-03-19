import { Button, Modal, Badge } from './ui'
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
        <p className="text-sm text-gray-500">
          Previewing {steps.length} step(s) — checking file patterns and row counts.
        </p>

        {steps.map((step) => {
          const preview = previews[step.id]
          return (
            <div key={step.id} className="rounded border border-gray-200 p-3">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-xs font-semibold text-gray-500">#{step.sequence}</span>
                <span className="font-medium text-gray-900 text-sm">{step.object_name}</span>
                <Badge variant={operationVariant(step.operation)}>{step.operation}</Badge>
              </div>

              {!preview || preview.status === 'idle' ? (
                <span className="text-xs text-gray-400">Not fetched</span>
              ) : preview.status === 'loading' ? (
                <div className="flex items-center gap-2">
                  <span
                    aria-label="Loading preview"
                    className="h-4 w-4 rounded-full border-2 border-blue-500 border-t-transparent animate-spin"
                  />
                  <span className="text-xs text-gray-500">Loading…</span>
                </div>
              ) : preview.status === 'error' ? (
                <p className="text-xs text-red-700">{preview.message}</p>
              ) : (
                <>
                  <p className="text-xs font-semibold text-green-700">
                    {preview.data.matched_files.length} file(s) •{' '}
                    {preview.data.total_rows.toLocaleString()} rows
                  </p>
                  {preview.data.matched_files.map((f) => (
                    <p key={f.filename} className="text-xs text-gray-600 font-mono mt-0.5">
                      {f.filename} — {f.row_count.toLocaleString()} rows
                    </p>
                  ))}
                  {preview.data.matched_files.length === 0 && (
                    <p className="text-xs text-amber-600 mt-1">
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
