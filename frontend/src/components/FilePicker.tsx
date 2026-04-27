import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faFolder, faChevronRight, faFile } from '@fortawesome/free-solid-svg-icons'
import { filesApi } from '../api/endpoints'
import { formatApiErrorStrict } from '../api/errors'
import type { InputDirectoryEntry } from '../api/types'

interface FilePickerProps {
  source: string
  onSelect: (path: string) => void
  onClose: () => void
}

export default function FilePicker({ source, onSelect, onClose }: FilePickerProps) {
  const [currentPath, setCurrentPath] = useState('')
  const segments = currentPath ? currentPath.split('/').filter(Boolean) : []

  useEffect(() => {
    setCurrentPath('')
  }, [source])

  const {
    data: entries = [],
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['files', 'input', source, currentPath],
    queryFn: () => filesApi.listInput(currentPath, source),
  })

  function navigate(path: string) {
    setCurrentPath(path)
  }

  return (
    <div className="mt-2 rounded-md border border-border-base bg-surface-sunken overflow-hidden">
      {/* Breadcrumb */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border-base bg-surface-raised">
        <nav aria-label="File picker breadcrumb" className="flex items-center gap-1 text-xs flex-wrap min-w-0">
          <button
            type="button"
            onClick={() => navigate('')}
            className={`transition-colors ${segments.length === 0 ? 'font-semibold text-content-primary' : 'text-content-link hover:text-accent-hover'}`}
          >
            {source === 'local-output' ? 'Output Files' : 'Input Files'}
          </button>
          {segments.map((seg, i) => {
            const segPath = segments.slice(0, i + 1).join('/')
            const isLast = i === segments.length - 1
            return (
              <span key={segPath} className="flex items-center gap-1">
                <FontAwesomeIcon icon={faChevronRight} className="text-content-muted text-[10px]" aria-hidden="true" />
                {isLast ? (
                  <span className="font-semibold text-content-primary truncate max-w-[8rem]">{seg}</span>
                ) : (
                  <button
                    type="button"
                    onClick={() => navigate(segPath)}
                    className="text-content-link hover:text-accent-hover transition-colors truncate max-w-[8rem]"
                  >
                    {seg}
                  </button>
                )}
              </span>
            )
          })}
        </nav>
        <button
          type="button"
          onClick={onClose}
          className="ml-2 shrink-0 text-xs text-content-muted hover:text-content-secondary transition-colors"
        >
          Close
        </button>
      </div>

      {/* Entry list */}
      <ul className="max-h-48 overflow-y-auto divide-y divide-border-base">
        {isLoading && (
          <li className="px-3 py-3 text-xs text-content-muted italic">Loading…</li>
        )}
        {isError && (
          <li className="px-3 py-3 text-xs text-error-text">
            {formatApiErrorStrict(error, 'Could not load files for this source.')}
          </li>
        )}
        {!isLoading && !isError && entries.length === 0 && (
          <li className="px-3 py-3 text-xs text-content-muted italic">No CSV files found here.</li>
        )}
        {!isError && entries.map((entry: InputDirectoryEntry) => (
          <li key={entry.path}>
            <button
              type="button"
              onClick={() => entry.kind === 'directory' ? navigate(entry.path) : onSelect(entry.path)}
              className="w-full text-left px-3 py-2 flex items-center gap-2 text-sm hover:bg-surface-hover transition-colors"
            >
              <FontAwesomeIcon
                icon={entry.kind === 'directory' ? faFolder : faFile}
                className="text-content-muted shrink-0"
                aria-hidden="true"
              />
              <span className="truncate text-content-primary">{entry.name}</span>
              {entry.kind === 'file' && entry.row_count != null && (
                <span className="ml-auto shrink-0 text-xs text-content-muted">
                  {entry.row_count.toLocaleString()} rows
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
