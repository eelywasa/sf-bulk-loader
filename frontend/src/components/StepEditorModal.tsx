import clsx from 'clsx'
import { Button, Modal, ComboInput } from './ui'
import FilePicker from './FilePicker'
import type { InputConnection, LoadStep } from '../api/types'
import { OPERATIONS, type StepFormData } from '../pages/planEditorUtils'
import { LABEL_CLASS, INPUT_CLASS, SELECT_CLASS, HELPER_TEXT_CLASS, ALERT_ERROR } from './ui/formStyles'

interface StepEditorModalProps {
  open: boolean
  editingStep: LoadStep | null
  stepForm: StepFormData
  stepFormErrors: string[]
  sfObjects: string[]
  sfObjectsLoading: boolean
  columnHeaders: string[]
  patternIsLiteral: boolean
  patternPreviewLoading: boolean
  showFilePicker: boolean
  connectionId: string
  inputConnections: InputConnection[]
  isSaving: boolean
  onChange: <K extends keyof StepFormData>(field: K, value: StepFormData[K]) => void
  onInputSourceChange: (value: string) => void
  onToggleFilePicker: () => void
  onFileSelect: (path: string) => void
  onSave: () => void
  onClose: () => void
}

export default function StepEditorModal({
  open,
  editingStep,
  stepForm,
  stepFormErrors,
  sfObjects,
  sfObjectsLoading,
  columnHeaders,
  patternIsLiteral,
  patternPreviewLoading,
  showFilePicker,
  connectionId,
  inputConnections,
  isSaving,
  onChange,
  onInputSourceChange,
  onToggleFilePicker,
  onFileSelect,
  onSave,
  onClose,
}: StepEditorModalProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={editingStep ? 'Edit Step' : 'Add Step'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={isSaving}>
            Cancel
          </Button>
          <Button loading={isSaving} onClick={onSave}>
            {editingStep ? 'Save Changes' : 'Add Step'}
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          onSave()
        }}
        className="space-y-4"
        noValidate
      >
        {stepFormErrors.length > 0 && (
          <div role="alert" className={`${ALERT_ERROR} space-y-1`}>
            {stepFormErrors.map((msg, i) => (
              <p key={i}>{msg}</p>
            ))}
          </div>
        )}

        {/* Salesforce object */}
        <div>
          <label htmlFor="step-object" className={LABEL_CLASS}>
            Salesforce Object <span className="text-red-500">*</span>
          </label>
          <ComboInput
            id="step-object"
            value={stepForm.object_name}
            onChange={(v) => onChange('object_name', v)}
            options={sfObjects}
            loading={sfObjectsLoading}
            loadingMessage="Loading objects…"
            placeholder="Account"
            inputClassName={INPUT_CLASS}
          />
          {connectionId === '' && (
            <p className={HELPER_TEXT_CLASS}>
              Select a connection on the plan to load object suggestions.
            </p>
          )}
        </div>

        {/* Operation */}
        <div>
          <label htmlFor="step-operation" className={LABEL_CLASS}>
            Operation <span className="text-red-500">*</span>
          </label>
          <select
            id="step-operation"
            value={stepForm.operation}
            onChange={(e) => onChange('operation', e.target.value)}
            className={SELECT_CLASS}
          >
            {OPERATIONS.map((op) => (
              <option key={op.value} value={op.value}>
                {op.label}
              </option>
            ))}
          </select>
        </div>

        {/* CSV file pattern */}
        <div>
          <label htmlFor="step-input-source" className={LABEL_CLASS}>
            Input Source
          </label>
          <select
            id="step-input-source"
            value={stepForm.input_connection_id}
            onChange={(e) => onInputSourceChange(e.target.value)}
            className={SELECT_CLASS}
          >
            <option value="">Local files</option>
            {inputConnections.map((inputConnection) => (
              <option key={inputConnection.id} value={inputConnection.id}>
                {inputConnection.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="step-pattern" className={LABEL_CLASS}>
            CSV File Pattern <span className="text-red-500">*</span>
          </label>
          <div className="flex gap-2">
            <input
              id="step-pattern"
              type="text"
              required
              value={stepForm.csv_file_pattern}
              onChange={(e) => {
                onChange('csv_file_pattern', e.target.value)
                if (showFilePicker) onToggleFilePicker()
              }}
              placeholder="accounts_*.csv"
              className={clsx(INPUT_CLASS, 'font-mono flex-1')}
            />
            <Button type="button" variant="secondary" size="sm" onClick={onToggleFilePicker}>
              Browse
            </Button>
          </div>
          {showFilePicker && (
            <FilePicker
              source={stepForm.input_connection_id || 'local'}
              onSelect={(path) => {
                onFileSelect(path)
              }}
              onClose={onToggleFilePicker}
            />
          )}
        </div>

        {/* External ID — shown only for upsert */}
        {stepForm.operation === 'upsert' && (
          <div>
            <label htmlFor="step-ext-id" className={LABEL_CLASS}>
              External ID Field <span className="text-red-500">*</span>
            </label>
            <ComboInput
              id="step-ext-id"
              value={stepForm.external_id_field}
              onChange={(v) => onChange('external_id_field', v)}
              options={columnHeaders}
              loading={patternPreviewLoading && patternIsLiteral}
              placeholder="ExternalId__c"
              inputClassName={INPUT_CLASS}
            />
            {!patternIsLiteral && stepForm.csv_file_pattern.length > 0 && (
              <p className={HELPER_TEXT_CLASS}>
                Enter a literal file path (no wildcards) to load column suggestions.
              </p>
            )}
          </div>
        )}

        {/* Partition size */}
        <div>
          <label htmlFor="step-partition" className={LABEL_CLASS}>
            Partition Size
          </label>
          <input
            id="step-partition"
            type="number"
            min="1"
            value={stepForm.partition_size}
            onChange={(e) => onChange('partition_size', e.target.value)}
            className={INPUT_CLASS}
          />
        </div>

        {/* Assignment Rule ID */}
        <div>
          <label htmlFor="step-assignment-rule" className={LABEL_CLASS}>
            Assignment Rule ID{' '}
            <span className="text-sm font-normal text-content-muted">(optional)</span>
          </label>
          <input
            id="step-assignment-rule"
            type="text"
            value={stepForm.assignment_rule_id}
            onChange={(e) => onChange('assignment_rule_id', e.target.value)}
            placeholder="01Q…"
            className={clsx(INPUT_CLASS, 'font-mono')}
          />
        </div>
      </form>
    </Modal>
  )
}
