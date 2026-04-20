import { useState } from 'react'
import clsx from 'clsx'
import { Button, Modal, ComboInput } from './ui'
import FilePicker from './FilePicker'
import type { InputConnection, LoadStep, StepPreviewQueryPlan } from '../api/types'
import { OPERATIONS, isQueryOp, type StepFormData } from '../pages/planEditorUtils'
import {
  LABEL_CLASS,
  INPUT_CLASS,
  SELECT_CLASS,
  TEXTAREA_CLASS,
  HELPER_TEXT_CLASS,
  ALERT_ERROR,
  ALERT_SUCCESS,
} from './ui/formStyles'
import { stepsApi } from '../api/endpoints'

// ─── Types ────────────────────────────────────────────────────────────────────

type SoqlValidationState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'valid'; sobjectType: string; leadingOperation: string; plan: StepPreviewQueryPlan }
  | { status: 'invalid'; error: string }
  | { status: 'error'; message: string }

// ─── Props ────────────────────────────────────────────────────────────────────

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
  planId: string | undefined
  inputConnections: InputConnection[]
  isSaving: boolean
  onChange: <K extends keyof StepFormData>(field: K, value: StepFormData[K]) => void
  onInputSourceChange: (value: string) => void
  onToggleFilePicker: () => void
  onFileSelect: (path: string) => void
  onSave: () => void
  onClose: () => void
}

// ─── Component ────────────────────────────────────────────────────────────────

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
  planId,
  inputConnections,
  isSaving,
  onChange,
  onInputSourceChange,
  onToggleFilePicker,
  onFileSelect,
  onSave,
  onClose,
}: StepEditorModalProps) {
  const queryMode = isQueryOp(stepForm.operation)
  const [soqlValidation, setSoqlValidation] = useState<SoqlValidationState>({ status: 'idle' })

  // Reset SOQL validation state when the operation changes or modal opens/closes
  function handleOperationChange(value: string) {
    onChange('operation', value)
    setSoqlValidation({ status: 'idle' })
  }

  function handleSoqlChange(value: string) {
    onChange('soql', value)
    // Invalidate previous validation result when the SOQL text changes
    setSoqlValidation({ status: 'idle' })
  }

  async function handleValidateSoql() {
    if (!planId) {
      setSoqlValidation({
        status: 'error',
        message: 'No plan selected — save the plan before validating SOQL.',
      })
      return
    }
    const soql = stepForm.soql.trim()
    if (!soql) return
    setSoqlValidation({ status: 'loading' })
    try {
      const result = await stepsApi.validateSoql(planId, soql)
      if (result.valid && result.plan) {
        setSoqlValidation({
          status: 'valid',
          sobjectType: result.plan.sobjectType,
          leadingOperation: result.plan.leadingOperation,
          plan: result.plan,
        })
      } else {
        setSoqlValidation({
          status: 'invalid',
          error: result.error ?? 'SOQL validation failed.',
        })
      }
    } catch (err) {
      setSoqlValidation({
        status: 'error',
        message: err instanceof Error ? err.message : 'Validation request failed.',
      })
    }
  }

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
          {queryMode && (
            <p className={HELPER_TEXT_CLASS}>
              Free-text label for this query step — not validated against the SOQL FROM clause.
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
            onChange={(e) => handleOperationChange(e.target.value)}
            className={SELECT_CLASS}
          >
            {OPERATIONS.map((op) => (
              <option key={op.value} value={op.value}>
                {op.label}
              </option>
            ))}
          </select>
        </div>

        {queryMode ? (
          /* ── Query mode: SOQL textarea + Validate action ─────────────────── */
          <div>
            <label htmlFor="step-soql" className={LABEL_CLASS}>
              SOQL Query <span className="text-red-500">*</span>
            </label>
            <textarea
              id="step-soql"
              rows={5}
              required
              value={stepForm.soql}
              onChange={(e) => handleSoqlChange(e.target.value)}
              placeholder="SELECT Id, Name FROM Account WHERE CreatedDate = LAST_N_DAYS:30"
              className={clsx(TEXTAREA_CLASS, 'font-mono text-xs')}
            />
            <p className={HELPER_TEXT_CLASS}>
              Must contain SELECT and FROM. Server-side validation is authoritative.
            </p>

            {/* Validate SOQL action */}
            <div className="mt-2 flex items-center gap-2">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={soqlValidation.status === 'loading'}
                disabled={!planId || !stepForm.soql.trim()}
                onClick={() => void handleValidateSoql()}
              >
                Validate SOQL
              </Button>
              {!planId && (
                <span className="text-xs text-content-muted">
                  Save the plan first to validate SOQL against Salesforce.
                </span>
              )}
            </div>

            {/* Validation result */}
            {soqlValidation.status === 'valid' && (
              <div className={`${ALERT_SUCCESS} mt-2`}>
                <p className="font-medium">SOQL is valid</p>
                <p className="text-xs mt-0.5">
                  Object: {soqlValidation.sobjectType} · Operation: {soqlValidation.leadingOperation}
                </p>
              </div>
            )}
            {soqlValidation.status === 'invalid' && (
              <div className={`${ALERT_ERROR} mt-2`}>
                <p className="font-medium">Validation failed</p>
                <p className="text-xs mt-0.5 font-mono whitespace-pre-wrap">{soqlValidation.error}</p>
              </div>
            )}
            {soqlValidation.status === 'error' && (
              <div className={`${ALERT_ERROR} mt-2`}>
                <p>{soqlValidation.message}</p>
              </div>
            )}
          </div>
        ) : (
          /* ── DML mode: CSV file pattern + partition size ─────────────────── */
          <>
            {/* Input Source */}
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
                <option value="">Local input files</option>
                <option value="local-output">Local output files (prior run results)</option>
                {inputConnections.map((inputConnection) => (
                  <option key={inputConnection.id} value={inputConnection.id}>
                    {inputConnection.name}
                  </option>
                ))}
              </select>
              {stepForm.input_connection_id === 'local-output' && (
                <p className={HELPER_TEXT_CLASS}>
                  Chain a prior run's output (e.g. query results) into this step.
                  Paths are relative to the local output directory.
                </p>
              )}
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
          </>
        )}

        {/* Assignment Rule ID — shown for all operations */}
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
