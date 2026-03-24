import { useParams, Link, useNavigate } from 'react-router-dom'
import { Button, Modal } from '../components/ui'
import { ALERT_ERROR } from '../components/ui/formStyles'
import PlanForm from '../components/PlanForm'
import StepList from '../components/StepList'
import StepEditorModal from '../components/StepEditorModal'
import PreflightPreviewModal from '../components/PreflightPreviewModal'
import { usePlanEditorState } from '../hooks/usePlanEditorState'
import { useStepPreview } from '../hooks/useStepPreview'

export default function PlanEditor() {
  const { id } = useParams<{ id: string }>()
  const isNew = id === 'new'
  const navigate = useNavigate()

  const {
    form,
    formErrors,
    setPlanField,
    handleSavePlan,
    isSavingPlan,
    stepModalOpen,
    editingStep,
    stepForm,
    stepFormErrors,
    showFilePicker,
    setShowFilePicker,
    deleteStepTarget,
    setDeleteStepTarget,
    openStepModal,
    closeStepModal,
    setStepField,
    handleInputSourceChange,
    handleSaveStep,
    isSavingStep,
    patternIsLiteral,
    columnHeaders,
    patternPreviewLoading,
    connectionId,
    plan,
    planLoading,
    planError,
    connections,
    inputConnections,
    sfObjects,
    sfObjectsLoading,
    sortedSteps,
    handleMoveUp,
    handleMoveDown,
    reorderMutation,
    startRunMutation,
    deleteStepMutation,
  } = usePlanEditorState(id)

  const { previews, preflightOpen, setPreflightOpen, handlePreviewStep, handlePreflight } =
    useStepPreview(isNew ? undefined : id)

  return (
    <div className="p-6 space-y-6">
      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <nav className="flex items-center gap-2 text-sm text-content-muted mb-1">
            <Link to="/plans" className="hover:text-content-primary">
              Load Plans
            </Link>
            <span>›</span>
            <span className="text-content-primary">
              {isNew ? 'New Plan' : (plan?.name ?? `Plan ${id}`)}
            </span>
          </nav>
          <h1 className="text-2xl font-bold text-content-primary">
            {isNew ? 'New Load Plan' : 'Edit Load Plan'}
          </h1>
        </div>

        <div className="flex gap-2 shrink-0">
          {!isNew && sortedSteps.length > 0 && (
            <Button variant="secondary" onClick={() => void handlePreflight(sortedSteps)}>
              Run Preflight
            </Button>
          )}
          {!isNew && (
            <Button
              variant="secondary"
              loading={startRunMutation.isPending}
              onClick={() => startRunMutation.mutate()}
            >
              Start Run
            </Button>
          )}
          <Button loading={isSavingPlan} onClick={handleSavePlan}>
            {isNew ? 'Save Plan' : 'Save Changes'}
          </Button>
        </div>
      </div>

      {/* ── Loading state (edit mode only) ──────────────────────────────────── */}
      {!isNew && planLoading && (
        <div className="flex justify-center py-16">
          <span
            aria-label="Loading"
            className="h-7 w-7 rounded-full border-2 border-blue-600 border-t-transparent animate-spin"
          />
        </div>
      )}

      {/* ── Error state (edit mode only) ────────────────────────────────────── */}
      {!isNew && planError && (
        <div className={ALERT_ERROR}>
          <p>
            Failed to load plan:{' '}
            {planError instanceof Error ? planError.message : 'Unknown error'}
          </p>
          <div className="mt-3">
            <Button variant="secondary" onClick={() => navigate('/plans')}>
              Back to Plans
            </Button>
          </div>
        </div>
      )}

      {/* ── Content (hidden while loading/errored in edit mode) ─────────────── */}
      {(isNew || (!planLoading && !planError)) && (
        <>
          <PlanForm
            form={form}
            formErrors={formErrors}
            connections={connections}
            onChange={setPlanField}
          />

          {/* New plan: save-first hint */}
          {isNew && (
            <div className="rounded-md border border-info-border bg-info-bg p-4 text-sm text-info-text">
              Save the plan first, then you can add load steps.
            </div>
          )}

          {/* Load Steps */}
          {!isNew && (
            <StepList
              steps={sortedSteps}
              previews={previews}
              inputConnections={inputConnections}
              reorderPending={reorderMutation.isPending}
              onEdit={(step) => openStepModal(step)}
              onDelete={(step) => setDeleteStepTarget(step)}
              onMoveUp={handleMoveUp}
              onMoveDown={handleMoveDown}
              onPreview={(step) => void handlePreviewStep(step)}
              onAddStep={() => openStepModal()}
            />
          )}

          <StepEditorModal
            open={stepModalOpen}
            editingStep={editingStep}
            stepForm={stepForm}
            stepFormErrors={stepFormErrors}
            sfObjects={sfObjects}
            sfObjectsLoading={sfObjectsLoading}
            columnHeaders={columnHeaders}
            patternIsLiteral={patternIsLiteral}
            patternPreviewLoading={patternPreviewLoading}
            showFilePicker={showFilePicker}
            connectionId={connectionId}
            inputConnections={inputConnections}
            isSaving={isSavingStep}
            onChange={setStepField}
            onInputSourceChange={handleInputSourceChange}
            onToggleFilePicker={() => setShowFilePicker((v) => !v)}
            onFileSelect={(path) => {
              setStepField('csv_file_pattern', path)
              setShowFilePicker(false)
            }}
            onSave={handleSaveStep}
            onClose={closeStepModal}
          />

          {/* Delete step confirmation */}
          <Modal
            open={deleteStepTarget !== null}
            onClose={() => setDeleteStepTarget(null)}
            size="sm"
            title="Delete Step"
            footer={
              <>
                <Button
                  variant="secondary"
                  onClick={() => setDeleteStepTarget(null)}
                  disabled={deleteStepMutation.isPending}
                >
                  Cancel
                </Button>
                <Button
                  variant="danger"
                  loading={deleteStepMutation.isPending}
                  onClick={() =>
                    deleteStepTarget && deleteStepMutation.mutate(deleteStepTarget.id)
                  }
                >
                  Delete
                </Button>
              </>
            }
          >
            <p className="text-sm text-content-secondary">
              Delete the step for{' '}
              <span className="font-semibold">{deleteStepTarget?.object_name}</span>? This cannot
              be undone.
            </p>
          </Modal>

          <PreflightPreviewModal
            open={preflightOpen}
            steps={sortedSteps}
            previews={previews}
            onClose={() => setPreflightOpen(false)}
          />
        </>
      )}
    </div>
  )
}
