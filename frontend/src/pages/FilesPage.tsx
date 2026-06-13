import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faFolder, faChevronRight } from '@fortawesome/free-solid-svg-icons'
import { filesApi, getRuntimeConfig, inputConnectionsApi } from '../api/endpoints'
import { formatApiErrorStrict } from '../api/errors'
import type { InputConnection, InputDirectoryEntry, StorageLocation } from '../api/types'
import { Card, CsvPreviewPanel, EmptyState, Spinner } from '../components/ui'
import { ALERT_ERROR, LABEL_CLASS, SELECT_CLASS } from '../components/ui/formStyles'
import { usePermission } from '../hooks/usePermission'

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
        Files
      </button>
      {segments.map((seg, i) => {
        const segPath = segments.slice(0, i + 1).join('/')
        const isLast = i === segments.length - 1
        return (
          <span key={segPath} className="flex items-center gap-1">
            <FontAwesomeIcon icon={faChevronRight} className="text-content-muted text-xs" aria-hidden="true" />
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
      <ul role="listbox" aria-label="Files" className="divide-y divide-border-base">
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
                    className="text-content-muted shrink-0"
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

// ─── Storage location line ─────────────────────────────────────────────────────

interface StorageLocationLineProps {
  location: StorageLocation | null
  appDistribution: string
}

/**
 * Tells the operator *where* the listed files physically live (SFBL-296).
 * S3 deployments show the bucket URI; filesystem deployments show the directory
 * path, with a deep link to Storage Settings on desktop.
 */
function StorageLocationLine({ location, appDistribution }: StorageLocationLineProps) {
  if (!location) return null

  if (location.provider === 's3') {
    return (
      <p className="mt-1 text-xs text-content-muted" data-testid="storage-location">
        Stored in <span className="font-mono">{location.uri}</span>
      </p>
    )
  }

  // Filesystem provider — directory path, plus a Storage Settings link on desktop.
  const isDesktop = appDistribution === 'desktop'
  return (
    <p className="mt-1 text-xs text-content-muted" data-testid="storage-location">
      Stored in <span className="font-mono">{location.uri}</span>
      {isDesktop ? (
        <>
          {' · '}
          <Link to="/settings" className="text-content-link hover:text-accent-hover">
            Configured in Storage Settings
          </Link>
        </>
      ) : (
        <> · container-mounted volume</>
      )}
    </p>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function FilesPage() {
  const [currentPath, setCurrentPath] = useState('')
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [source, setSource] = useState<string>('local')
  const canViewContents = usePermission('files.view_contents')

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

  const { data: runtime } = useQuery({
    queryKey: ['runtime-config'],
    queryFn: () => getRuntimeConfig(),
    staleTime: 5 * 60 * 1000,
  })

  const isOutputSource = source === 'local-output'

  // Where the files for the active source physically live (SFBL-296).
  const activeLocation: StorageLocation | null = (() => {
    if (source === 'local') return runtime?.storage_locations?.input ?? null
    if (source === 'local-output') return runtime?.storage_locations?.output ?? null
    const conn = inputConnections.find((c) => c.id === source)
    if (!conn) return null
    const prefix = conn.root_prefix ? `/${conn.root_prefix.replace(/^\/+|\/+$/g, '')}` : ''
    return {
      provider: 's3',
      uri: `s3://${conn.bucket}${prefix}`,
      bucket: conn.bucket,
      region: conn.region ?? null,
      prefix: conn.root_prefix ?? null,
    }
  })()

  const locationLine = (
    <StorageLocationLine
      location={activeLocation}
      appDistribution={runtime?.app_distribution ?? ''}
    />
  )

  const {
    data: entries,
    isLoading: filesLoading,
    isError: filesError,
    error: filesErr,
  } = useQuery({
    queryKey: ['files', source, currentPath],
    queryFn: () =>
      isOutputSource
        ? filesApi.listOutput(currentPath)
        : filesApi.listInput(currentPath, source),
  })

  // ── Source selector ────────────────────────────────────────────────────────

  const sourceSelector = (
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
        <option value="local">Input Files</option>
        <option value="local-output">Output Files</option>
        {inputConnections.map((conn) => (
          <option key={conn.id} value={conn.id}>{conn.name}</option>
        ))}
      </select>
    </div>
  )

  const sourceDescription =
    source === 'local'
      ? 'Browse and preview input CSV files.'
      : source === 'local-output'
        ? 'Browse and preview result CSV files written by load runs.'
        : 'Browse and preview CSV files from the selected storage connection.'

  const header = (
    <div>
      <h1 className="text-2xl font-bold text-content-primary">Files</h1>
      <p className="mt-1 text-sm text-content-muted">{sourceDescription}</p>
      {sourceSelector}
      {locationLine}
    </div>
  )

  // ── Loading state ──────────────────────────────────────────────────────────

  if (filesLoading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-[200px]">
        <Spinner size="lg" />
      </div>
    )
  }

  // ── Error state ────────────────────────────────────────────────────────────

  if (filesError) {
    const message =
      formatApiErrorStrict(filesErr, 'Failed to load files')
    return (
      <div className="p-6 space-y-6">
        {header}
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
        {header}
        <Breadcrumb currentPath={currentPath} onNavigate={handleNavigate} />
        <EmptyState
          title="No files found"
          description={
            source === 'local'
              ? 'Add CSV files to the input storage location to see them here.'
              : source === 'local-output'
                ? 'No result files found. Run a load plan to generate output files.'
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

  if (!canViewContents) {
    // Viewer role: show a callout explaining previews are restricted
    previewPanel = (
      <Card>
        <div className="py-6 px-4 text-center space-y-2">
          <p className="text-sm font-medium text-content-secondary">File previews are not available for your role.</p>
          <p className="text-xs text-content-muted">Contact your administrator to request access.</p>
        </div>
      </Card>
    )
  } else if (!selectedFile) {
    previewPanel = <PreviewEmpty />
  } else {
    previewPanel = (
      <Card>
        <CsvPreviewPanel
          key={selectedFile}
          queryKey={['files', 'preview', source, selectedFile]}
          fetchPage={(params) =>
            isOutputSource
              ? filesApi.previewOutput(selectedFile, params)
              : filesApi.previewInput(selectedFile, params, source)
          }
          filename={selectedEntry?.name}
        />
      </Card>
    )
  }

  // ── Main layout ────────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-6">
      {header}

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
