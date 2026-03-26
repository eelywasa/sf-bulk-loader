import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faFolder, faChevronRight } from '@fortawesome/free-solid-svg-icons'
import { filesApi, inputConnectionsApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import type { InputConnection, InputDirectoryEntry } from '../api/types'
import { Card, CsvPreviewPanel, EmptyState } from '../components/ui'
import { ALERT_ERROR, LABEL_CLASS, SELECT_CLASS } from '../components/ui/formStyles'

// ─── Helpers ──────────────────────────────────────────────────────────────────

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// ─── Breadcrumb ───────────────────────────────────────────────────────────────

interface BreadcrumbProps {
  currentPath: string
  onNavigate: (path: string) => void
}

function Breadcrumb({ currentPath, onNavigate }: BreadcrumbProps) {
  const segments = currentPath ? currentPath.split('/').filter(Boolean) : []

  return (
    <nav aria-label="Directory breadcrumb" className="flex items-center gap-1 text-sm flex-wrap">
      <button
        type="button"
        onClick={() => onNavigate('')}
        className={`transition-colors ${segments.length === 0 ? 'font-semibold text-content-primary' : 'text-content-link hover:text-accent-hover'}`}
      >
        Input Files
      </button>
      {segments.map((seg, i) => {
        const segPath = segments.slice(0, i + 1).join('/')
        const isLast = i === segments.length - 1
        return (
          <span key={segPath} className="flex items-center gap-1">
            <FontAwesomeIcon icon={faChevronRight} className="text-content-muted text-xs" />
            {isLast ? (
              <span className="font-semibold text-content-primary">{seg}</span>
            ) : (
              <button
                type="button"
                onClick={() => onNavigate(segPath)}
                className="text-content-link hover:text-accent-hover transition-colors"
              >
                {seg}
              </button>
            )}
          </span>
        )
      })}
    </nav>
  )
}

// ─── File list panel ─────────────────────────────────────────────────────────

interface FileListProps {
  entries: InputDirectoryEntry[]
  selected: string | null
  onSelect: (path: string) => void
  onNavigate: (path: string) => void
}

function FileList({ entries, selected, onSelect, onNavigate }: FileListProps) {
  return (
    <Card padding={false}>
      <ul role="listbox" aria-label="Input files" className="divide-y divide-border-base">
        {entries.map((entry) => {
          const isSelected = entry.kind === 'file' && selected === entry.path
          return (
            <li
              key={entry.path}
              role="option"
              aria-selected={isSelected}
            >
              <button
                type="button"
                onClick={() =>
                  entry.kind === 'directory' ? onNavigate(entry.path) : onSelect(entry.path)
                }
                className={`w-full text-left px-4 py-3 transition-colors flex items-center gap-3 ${
                  isSelected
                    ? 'bg-surface-selected text-content-selected'
                    : 'hover:bg-surface-hover text-content-primary'
                }`}
              >
                {entry.kind === 'directory' && (
                  <FontAwesomeIcon
                    icon={faFolder}
                    className="text-amber-400 shrink-0"
                    aria-hidden="true"
                  />
                )}
                <span className="min-w-0 flex-1">
                  <p className="text-sm font-medium truncate">{entry.name}</p>
                  {entry.kind === 'file' && (entry.size_bytes != null || entry.row_count != null) && (
                    <p className="text-xs text-content-muted mt-0.5">
                      {[
                        entry.size_bytes != null ? formatFileSize(entry.size_bytes) : null,
                        entry.row_count != null ? `${entry.row_count.toLocaleString()} rows` : null,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </p>
                  )}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </Card>
  )
}

// ─── Preview panel ────────────────────────────────────────────────────────────

function PreviewEmpty() {
  return (
    <Card>
      <EmptyState
        title="No file selected"
        description="Select a file from the list to preview its contents."
      />
    </Card>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function FilesPage() {
  const [currentPath, setCurrentPath] = useState('')
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [source, setSource] = useState<string>('local')

  function handleNavigate(path: string) {
    setCurrentPath(path)
    setSelectedFile(null)
  }

  function handleSourceChange(newSource: string) {
    setSource(newSource)
    setCurrentPath('')
    setSelectedFile(null)
  }

  const { data: inputConnections = [] } = useQuery<InputConnection[]>({
    queryKey: ['input-connections'],
    queryFn: () => inputConnectionsApi.list(),
  })

  const {
    data: entries,
    isLoading: filesLoading,
    isError: filesError,
    error: filesErr,
  } = useQuery({
    queryKey: ['files', 'input', source, currentPath],
    queryFn: () => filesApi.listInput(currentPath, source),
  })

  // ── Source selector (shown when input connections exist) ───────────────────

  const sourceSelector = inputConnections.length > 0 ? (
    <div className="mt-3 flex items-center gap-2">
      <label htmlFor="source-select" className={LABEL_CLASS + ' mb-0 shrink-0'}>
        Source
      </label>
      <select
        id="source-select"
        value={source}
        onChange={(e) => handleSourceChange(e.target.value)}
        className={SELECT_CLASS + ' w-auto'}
      >
        <option value="local">Local files</option>
        {inputConnections.map((conn) => (
          <option key={conn.id} value={conn.id}>{conn.name}</option>
        ))}
      </select>
    </div>
  ) : null

  const sourceDescription =
    source === 'local'
      ? 'Browse and preview CSV files in the input directory.'
      : 'Browse and preview CSV files from the selected input source.'

  // ── Loading state ──────────────────────────────────────────────────────────

  if (filesLoading) {
    return (
      <div
        className="p-6 flex items-center justify-center min-h-[200px]"
        aria-label="Loading"
      >
        <div className="h-8 w-8 rounded-full border-4 border-blue-600 border-t-transparent animate-spin" />
      </div>
    )
  }

  // ── Error state ────────────────────────────────────────────────────────────

  if (filesError) {
    const message =
      filesErr instanceof ApiError ? filesErr.message : 'Failed to load input files'
    return (
      <div className="p-6 space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-content-primary">Input Files</h1>
          <p className="mt-1 text-sm text-content-muted">
            {sourceDescription}
          </p>
          {sourceSelector}
        </div>
        <div className={ALERT_ERROR}>
          <p>{message}</p>
        </div>
      </div>
    )
  }

  // ── Empty state ────────────────────────────────────────────────────────────

  if (!entries || entries.length === 0) {
    return (
      <div className="p-6 space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-content-primary">Input Files</h1>
          <p className="mt-1 text-sm text-content-muted">
            {sourceDescription}
          </p>
          {sourceSelector}
        </div>
        <Breadcrumb currentPath={currentPath} onNavigate={handleNavigate} />
        <EmptyState
          title="No input files found"
          description={
            source === 'local'
              ? 'Place CSV files in the /data/input directory to see them here.'
              : 'No files found in this location.'
          }
        />
      </div>
    )
  }

  // ── Preview panel content ──────────────────────────────────────────────────

  const selectedEntry =
    selectedFile != null
      ? entries.find(
          (entry): entry is InputDirectoryEntry & { kind: 'file' } =>
            entry.kind === 'file' && entry.path === selectedFile,
        )
      : null

  let previewPanel: React.ReactNode

  if (!selectedFile) {
    previewPanel = <PreviewEmpty />
  } else {
    previewPanel = (
      <Card>
        <CsvPreviewPanel
          queryKey={['files', 'preview', source, selectedFile]}
          fetchPage={(params) => filesApi.previewInput(selectedFile, params, source)}
          filename={selectedEntry?.name}
        />
      </Card>
    )
  }

  // ── Main layout ────────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-content-primary">Input Files</h1>
        <p className="mt-1 text-sm text-content-muted">
          {sourceDescription}
        </p>
        {sourceSelector}
      </div>

      <Breadcrumb currentPath={currentPath} onNavigate={handleNavigate} />

      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-6 items-start">
        <FileList
          entries={entries}
          selected={selectedFile}
          onSelect={setSelectedFile}
          onNavigate={handleNavigate}
        />
        <div className="min-w-0">{previewPanel}</div>
      </div>
    </div>
  )
}
