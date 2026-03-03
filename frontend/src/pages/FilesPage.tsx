import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { filesApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import { Card, EmptyState } from '../components/ui'

// ─── Helpers ──────────────────────────────────────────────────────────────────

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// ─── File list panel ─────────────────────────────────────────────────────────

interface FileListProps {
  filenames: Array<{ filename: string; size_bytes: number }>
  selected: string | null
  onSelect: (filename: string) => void
}

function FileList({ filenames, selected, onSelect }: FileListProps) {
  return (
    <Card padding={false}>
      <ul role="listbox" aria-label="Input files" className="divide-y divide-gray-100">
        {filenames.map((file) => (
          <li key={file.filename} role="option" aria-selected={selected === file.filename}>
            <button
              type="button"
              onClick={() => onSelect(file.filename)}
              className={`w-full text-left px-4 py-3 transition-colors ${
                selected === file.filename
                  ? 'bg-blue-50 text-blue-700'
                  : 'hover:bg-gray-50 text-gray-900'
              }`}
            >
              <p className="text-sm font-medium truncate">{file.filename}</p>
              <p className="text-xs text-gray-500 mt-0.5">{formatFileSize(file.size_bytes)}</p>
            </button>
          </li>
        ))}
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
  const [selectedFile, setSelectedFile] = useState<string | null>(null)

  const {
    data: files,
    isLoading: filesLoading,
    isError: filesError,
    error: filesErr,
  } = useQuery({
    queryKey: ['files', 'input'],
    queryFn: () => filesApi.listInput(),
  })

  const {
    data: preview,
    isLoading: previewLoading,
    isError: previewError,
    error: previewErr,
  } = useQuery({
    queryKey: ['files', 'preview', selectedFile],
    queryFn: () => filesApi.previewInput(selectedFile!, 25),
    enabled: !!selectedFile,
  })

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
          <h1 className="text-2xl font-bold text-gray-900">Input Files</h1>
          <p className="mt-1 text-sm text-gray-500">
            Browse and preview CSV files in the input directory.
          </p>
        </div>
        <div className="rounded-md bg-red-50 border border-red-200 p-4">
          <p className="text-sm text-red-700">{message}</p>
        </div>
      </div>
    )
  }

  // ── Empty state ────────────────────────────────────────────────────────────

  if (!files || files.length === 0) {
    return (
      <div className="p-6 space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Input Files</h1>
          <p className="mt-1 text-sm text-gray-500">
            Browse and preview CSV files in the input directory.
          </p>
        </div>
        <EmptyState
          title="No input files found"
          description="Place CSV files in the /data/input directory to see them here."
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
        <h1 className="text-2xl font-bold text-gray-900">Input Files</h1>
        <p className="mt-1 text-sm text-gray-500">
          Browse and preview CSV files in the input directory.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-6 items-start">
        <FileList
          filenames={files}
          selected={selectedFile}
          onSelect={setSelectedFile}
        />
        <div>{previewPanel}</div>
      </div>
    </div>
  )
}
