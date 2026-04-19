import { Card } from './ui'
import type { Connection, InputConnection } from '../api/types'
import { type PlanFormData } from '../pages/planEditorUtils'
import { LABEL_CLASS, INPUT_CLASS, SELECT_CLASS, TEXTAREA_CLASS, ALERT_ERROR } from './ui/formStyles'

interface PlanFormProps {
  form: PlanFormData
  formErrors: string[]
  connections: Connection[] | undefined
  outputConnections: InputConnection[]
  onChange: <K extends keyof PlanFormData>(field: K, value: PlanFormData[K]) => void
}

export default function PlanForm({ form, formErrors, connections, outputConnections, onChange }: PlanFormProps) {
  return (
    <Card title="Plan Details">
      <div className="space-y-4">
        {formErrors.length > 0 && (
          <div role="alert" className={`${ALERT_ERROR} space-y-1`}>
            {formErrors.map((msg, i) => (
              <p key={i}>{msg}</p>
            ))}
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {/* Name */}
          <div className="sm:col-span-2">
            <label htmlFor="plan-name" className={LABEL_CLASS}>
              Name <span className="text-red-500">*</span>
            </label>
            <input
              id="plan-name"
              type="text"
              required
              value={form.name}
              onChange={(e) => onChange('name', e.target.value)}
              placeholder="Q1 Data Migration"
              className={INPUT_CLASS}
            />
          </div>

          {/* Description */}
          <div className="sm:col-span-2">
            <label htmlFor="plan-desc" className={LABEL_CLASS}>
              Description
            </label>
            <textarea
              id="plan-desc"
              rows={2}
              value={form.description}
              onChange={(e) => onChange('description', e.target.value)}
              placeholder="Optional description"
              className={TEXTAREA_CLASS}
            />
          </div>

          {/* Connection */}
          <div className="sm:col-span-2">
            <label htmlFor="plan-connection" className={LABEL_CLASS}>
              Connection <span className="text-red-500">*</span>
            </label>
            <select
              id="plan-connection"
              value={form.connection_id}
              onChange={(e) => onChange('connection_id', e.target.value)}
              className={SELECT_CLASS}
            >
              <option value="">Select a connection…</option>
              {connections?.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          {/* Error threshold */}
          <div>
            <label htmlFor="plan-threshold" className={LABEL_CLASS}>
              Error Threshold (%)
            </label>
            <input
              id="plan-threshold"
              type="number"
              min="0"
              max="100"
              value={form.error_threshold_pct}
              onChange={(e) => onChange('error_threshold_pct', e.target.value)}
              className={INPUT_CLASS}
            />
          </div>

          {/* Max parallel jobs */}
          <div>
            <label htmlFor="plan-parallel" className={LABEL_CLASS}>
              Max Parallel Jobs
            </label>
            <input
              id="plan-parallel"
              type="number"
              min="1"
              max="25"
              value={form.max_parallel_jobs}
              onChange={(e) => onChange('max_parallel_jobs', e.target.value)}
              className={INPUT_CLASS}
            />
          </div>

          {/* Abort on step failure */}
          <div className="sm:col-span-2 flex items-center gap-3">
            <input
              id="plan-abort"
              type="checkbox"
              checked={form.abort_on_step_failure}
              onChange={(e) => onChange('abort_on_step_failure', e.target.checked)}
              className="h-4 w-4 rounded border-border-strong text-blue-600 focus:ring-border-focus"
            />
            <label htmlFor="plan-abort" className="text-sm font-medium text-content-secondary">
              Abort run if a step fails
            </label>
          </div>

          {/* Output Destination */}
          <div className="sm:col-span-2">
            <label htmlFor="plan-output-connection" className={LABEL_CLASS}>
              Output Destination
            </label>
            <select
              id="plan-output-connection"
              value={form.output_connection_id}
              onChange={(e) => onChange('output_connection_id', e.target.value)}
              className={SELECT_CLASS}
            >
              <option value="">Local filesystem (default)</option>
              {outputConnections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <p className="mt-1.5 text-xs text-content-muted">
              Tip: to keep output separate from input on the same bucket, use a storage connection with a distinct root prefix (e.g. <code className="font-mono">data/output/</code>).
            </p>
          </div>
        </div>
      </div>
    </Card>
  )
}
