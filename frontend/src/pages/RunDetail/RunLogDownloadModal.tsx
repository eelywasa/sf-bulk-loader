import { useCallback, useState } from 'react'
import { Button, Card } from '../../components/ui'
import { CHECKBOX_CLASS } from '../../components/ui/formStyles'
import { runsApi } from '../../api/endpoints'
import type { JobRecord } from '../../api/types'

interface RunLogDownloadProps {
  runId: string
  jobs: JobRecord[]
}

export function RunLogDownload({ runId, jobs }: RunLogDownloadProps) {
  const [includeSuccess, setIncludeSuccess] = useState(true)
  const [includeErrors, setIncludeErrors] = useState(true)
  const [includeUnprocessed, setIncludeUnprocessed] = useState(true)

  const noneSelected = !includeSuccess && !includeErrors && !includeUnprocessed
  const hasLogs = jobs.some(
    (j) => j.success_file_path || j.error_file_path || j.unprocessed_file_path,
  )

  const handleDownload = useCallback(() => {
    const url = runsApi.logsZipUrl(runId, {
      success: includeSuccess,
      errors: includeErrors,
      unprocessed: includeUnprocessed,
    })
    const a = document.createElement('a')
    a.href = url
    a.download = `run_${runId.slice(0, 8)}_logs.zip`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }, [runId, includeSuccess, includeErrors, includeUnprocessed])

  return (
    <Card title="Download Logs">
      <div className="space-y-4">
        <p className="text-sm text-content-muted">
          Select the log types to include in the ZIP download.
        </p>
        <div className="flex flex-wrap gap-6">
          {(
            [
              { id: 'success', label: 'Success Logs', checked: includeSuccess, set: setIncludeSuccess },
              { id: 'errors', label: 'Error Logs', checked: includeErrors, set: setIncludeErrors },
              {
                id: 'unprocessed',
                label: 'Unprocessed Records',
                checked: includeUnprocessed,
                set: setIncludeUnprocessed,
              },
            ] as const
          ).map(({ id: cbId, label, checked, set }) => (
            <label key={cbId} className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={checked}
                onChange={(e) => set(e.target.checked)}
                className={CHECKBOX_CLASS}
              />
              <span className="text-sm text-content-primary">{label}</span>
            </label>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="secondary"
            onClick={handleDownload}
            disabled={noneSelected || !hasLogs}
          >
            ↓ Download ZIP
          </Button>
          {!hasLogs && (
            <span className="text-xs text-content-muted italic">No log files available yet.</span>
          )}
        </div>
      </div>
    </Card>
  )
}
