import clsx from 'clsx'
import {
  DndContext,
  closestCenter,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Button, Card, Badge } from './ui'
import type { LoadStep, InputConnection } from '../api/types'
import { operationVariant, isQueryOp, computeStepLabel, type PreviewEntry } from '../pages/planEditorUtils'

interface StepListProps {
  steps: LoadStep[]
  previews: Record<string, PreviewEntry>
  inputConnections: InputConnection[]
  reorderPending: boolean
  onEdit: (step: LoadStep) => void
  onDelete: (step: LoadStep) => void
  onMoveUp: (step: LoadStep) => void
  onMoveDown: (step: LoadStep) => void
  onReorder: (stepIds: string[]) => void
  onPreview: (step: LoadStep) => void
  onAddStep: () => void
}

// ─── Per-row sortable item ────────────────────────────────────────────────────

interface SortableStepRowProps {
  step: LoadStep
  idx: number
  totalSteps: number
  preview: PreviewEntry | undefined
  inputConnections: InputConnection[]
  reorderPending: boolean
  onEdit: (step: LoadStep) => void
  onDelete: (step: LoadStep) => void
  onMoveUp: (step: LoadStep) => void
  onMoveDown: (step: LoadStep) => void
  onPreview: (step: LoadStep) => void
  allSteps: LoadStep[]
}

function SortableStepRow({
  step,
  idx,
  totalSteps,
  preview,
  inputConnections,
  reorderPending,
  onEdit,
  onDelete,
  onMoveUp,
  onMoveDown,
  onPreview,
  allSteps,
}: SortableStepRowProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: step.id,
    disabled: reorderPending,
  })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  }

  const upstreamStep = step.input_from_step_id
    ? allSteps.find((s) => s.id === step.input_from_step_id)
    : null
  const upstreamLabel = upstreamStep
    ? upstreamStep.name ||
      computeStepLabel(upstreamStep.sequence, upstreamStep.operation, upstreamStep.object_name)
    : null

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={clsx('py-4', isDragging && 'opacity-50 bg-surface-sunken rounded-md')}
    >
      {/* Step row */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-2 min-w-0">
          {/* Drag handle */}
          <button
            {...attributes}
            {...listeners}
            className={clsx(
              'mt-0.5 shrink-0 cursor-grab active:cursor-grabbing text-content-muted hover:text-content-secondary focus:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded px-0.5',
              reorderPending && 'cursor-not-allowed opacity-40',
            )}
            aria-label="Drag to reorder"
            tabIndex={0}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="12"
              height="16"
              viewBox="0 0 12 16"
              fill="currentColor"
              aria-hidden="true"
            >
              <circle cx="3" cy="3" r="1.5" />
              <circle cx="9" cy="3" r="1.5" />
              <circle cx="3" cy="8" r="1.5" />
              <circle cx="9" cy="8" r="1.5" />
              <circle cx="3" cy="13" r="1.5" />
              <circle cx="9" cy="13" r="1.5" />
            </svg>
          </button>

          <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-info-bg text-xs font-semibold text-info-text">
            {step.sequence}
          </span>
          <div className="min-w-0">
            <p className="font-medium text-content-primary text-sm">
              {step.name || step.object_name}
              {step.name && (
                <span className="ml-1 text-xs font-normal text-content-muted">
                  ({step.object_name})
                </span>
              )}
              <span className="ml-2">
                <Badge variant={operationVariant(step.operation)}>
                  {step.operation}
                </Badge>
              </span>
            </p>
            {/* Upstream chain badge */}
            {upstreamLabel && (
              <p className="text-xs text-content-muted mt-0.5 flex items-center gap-1">
                <span aria-hidden="true">→</span>
                <span data-testid="upstream-badge">from {upstreamLabel}</span>
              </p>
            )}
            {isQueryOp(step.operation) ? (
              <p className="text-xs text-content-muted mt-0.5 font-mono truncate">
                {step.soql ? step.soql.substring(0, 80) + (step.soql.length > 80 ? '…' : '') : '(no SOQL)'}
              </p>
            ) : (
              <p className="text-xs text-content-muted mt-0.5 font-mono truncate">
                <span className="not-italic font-sans text-content-muted mr-1">
                  {step.input_connection_id === 'local-output'
                    ? 'Local output'
                    : step.input_connection_id
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
            disabled={idx === totalSteps - 1 || reorderPending}
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
              {preview.data.note && step.input_from_step_id ? (
                <p className="font-medium text-info-text">{preview.data.note}</p>
              ) : (
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
}

// ─── StepList ────────────────────────────────────────────────────────────────

export default function StepList({
  steps,
  previews,
  inputConnections,
  reorderPending,
  onEdit,
  onDelete,
  onMoveUp,
  onMoveDown,
  onReorder,
  onPreview,
  onAddStep,
}: StepListProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const oldIndex = steps.findIndex((s) => s.id === active.id)
    const newIndex = steps.findIndex((s) => s.id === over.id)
    if (oldIndex === -1 || newIndex === -1) return
    const reordered = arrayMove(steps, oldIndex, newIndex)
    onReorder(reordered.map((s) => s.id))
  }

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
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext items={steps.map((s) => s.id)} strategy={verticalListSortingStrategy}>
            <div className="divide-y divide-border-base">
              {steps.map((step, idx) => (
                <SortableStepRow
                  key={step.id}
                  step={step}
                  idx={idx}
                  totalSteps={steps.length}
                  preview={previews[step.id]}
                  inputConnections={inputConnections}
                  reorderPending={reorderPending}
                  onEdit={onEdit}
                  onDelete={onDelete}
                  onMoveUp={onMoveUp}
                  onMoveDown={onMoveDown}
                  onPreview={onPreview}
                  allSteps={steps}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}
    </Card>
  )
}
