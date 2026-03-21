import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faFolder, faChevronRight } from '@fortawesome/free-solid-svg-icons'
import { filesApi, inputConnectionsApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import type { InputConnection, InputDirectoryEntry } from '../api/types'
import { Card, EmptyState } from '../components/ui'

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
        className={`transition-colors ${segments.length === 0 ? 'font-semibold text-gray-900 dark:text-gray-100' : 'text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300'}`}
      >
        Input Files
      </button>
      {segments.map((seg, i) => {
        const segPath = segments.slice(0, i + 1).join('/')
        const isLast = i === segments.length - 1
        return (
          <span key={segPath} className="flex items-center gap-1">
            <FontAwesomeIcon icon={faChevronRight} className="text-gray-400 dark:text-gray-500 text-xs" />
            {isLast ? (
              <span className="font-semibold text-gray-900 dark:text-gray-100">{seg}</span>
            ) : (
              <button
                type="button"
                onClick={() => onNavigate(segPath)}
                className="text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 transition-colors"
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
      <ul role="listbox" aria-label="Input files" className="divide-y divide-gray-100">
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
                    ? 'bg-blue-50 text-blue-700'
                    : 'hover:bg-gray-50 text-gray-900'
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
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
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

function PreviewLoading() {
  return (
    <Card>
      <div
        className="flex items-center justify-center min-h-[200px]"
        aria-label="Loading preview"
      >
        <div className="h-8 w-8 rounded-full border-4 border-blue-600 border-t-transparent animate-spin" />
      </div>
    </Card>
  )
}

function PreviewError({ message }: { message: string }) {
  return (
    <Card>
      <div className="rounded-md bg-red-50 border border-red-200 p-4">
        <p className="text-sm text-red-700">{message}</p>
      </div>
    </Card>
  )
}

interface PreviewTableProps {
  filename: string
  header: string[]
  rows: Record<string, string>[]
  rowCount: number
}

function PreviewTable({ filename, header, rows, rowCount }: PreviewTableProps) {
  return (
    <Card>
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-gray-900">{filename}</h2>
          <span className="text-sm text-gray-500">{rowCount.toLocaleString()} rows</span>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead>
              <tr>
                {header.map((col) => (
                  <th
                    key={col}
                    scope="col"
                    className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wide whitespace-nowrap bg-gray-50"
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((row, i) => (
                <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                  {header.map((col) => (
                    <td
                      key={col}
                      className="px-3 py-2 text-gray-700 whitespace-nowrap font-mono text-xs"
                    >
                      {row[col] ?? ''}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {rows.length < rowCount && (
          <p className="text-xs text-gray-400 text-right">
            Showing first {rows.length} of {rowCount.toLocaleString()} rows
          </p>
        )}
      </div>
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

  const {
    data: preview,
    isLoading: previewLoading,
    isError: previewError,
    error: previewErr,
  } = useQuery({
    queryKey: ['files', 'preview', source, selectedFile],
    queryFn: () => filesApi.previewInput(selectedFile!, 25, source),
    enabled: !!selectedFile,
  })

  // ── Source selector (shown when input connections exist) ───────────────────

  const sourceSelector = inputConnections.length > 0 ? (
    <div className="mt-3 flex items-center gap-2">
      <label htmlFor="source-select" className="text-sm font-medium text-gray-700 dark:text-gray-300 shrink-0">
        Source
      </label>
      <select
        id="source-select"
        value={source}
        onChange={(e) => handleSourceChange(e.target.value)}
        className="text-sm border border-gray-300 dark:border-gray-600 rounded-md px-3 py-1.5 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        <option value="local">Local files</option>
        {inputConnections.map((conn) => (
          <option key={conn.id} value={conn.id}>{conn.name}</option>
        ))}
      </select>
    </div>
  ) : null

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
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Input Files</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Browse and preview CSV files in the input directory.
          </p>
          {sourceSelector}
        </div>
        <div className="rounded-md bg-red-50 border border-red-200 p-4">
          <p className="text-sm text-red-700">{message}</p>
        </div>
      </div>
    )
  }

  // ── Empty state ────────────────────────────────────────────────────────────

  if (!entries || entries.length === 0) {
    return (
      <div className="p-6 space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Input Files</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Browse and preview CSV files in the input directory.
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

  let previewPanel: React.ReactNode

  if (!selectedFile) {
    previewPanel = <PreviewEmpty />
  } else if (previewLoading) {
    previewPanel = <PreviewLoading />
  } else if (previewError) {
    const msg =
      previewErr instanceof ApiError ? previewErr.message : 'Failed to load file preview'
    previewPanel = <PreviewError message={msg} />
  } else if (preview) {
    previewPanel = (
      <PreviewTable
        filename={preview.filename}
        header={preview.header}
        rows={preview.rows}
        rowCount={preview.row_count}
      />
    )
  }

  // ── Main layout ────────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Input Files</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Browse and preview CSV files in the input directory.
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
        <div>{previewPanel}</div>
      </div>
    </div>
  )
}
