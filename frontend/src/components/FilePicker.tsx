import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faFolder, faChevronRight, faFile } from '@fortawesome/free-solid-svg-icons'
import { filesApi } from '../api/endpoints'
import { ApiError } from '../api/client'
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
    <div className="mt-2 rounded-md border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 overflow-hidden">
      {/* Breadcrumb */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <nav aria-label="File picker breadcrumb" className="flex items-center gap-1 text-xs flex-wrap min-w-0">
          <button
            type="button"
            onClick={() => navigate('')}
            className={`transition-colors ${segments.length === 0 ? 'font-semibold text-gray-900 dark:text-gray-100' : 'text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300'}`}
          >
            Input Files
          </button>
          {segments.map((seg, i) => {
            const segPath = segments.slice(0, i + 1).join('/')
            const isLast = i === segments.length - 1
            return (
              <span key={segPath} className="flex items-center gap-1">
                <FontAwesomeIcon icon={faChevronRight} className="text-gray-400 dark:text-gray-500 text-[10px]" />
                {isLast ? (
                  <span className="font-semibold text-gray-900 dark:text-gray-100 truncate max-w-[8rem]">{seg}</span>
                ) : (
                  <button
                    type="button"
                    onClick={() => navigate(segPath)}
                    className="text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 transition-colors truncate max-w-[8rem]"
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
          className="ml-2 shrink-0 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
        >
          Close
        </button>
      </div>

      {/* Entry list */}
      <ul className="max-h-48 overflow-y-auto divide-y divide-gray-100 dark:divide-gray-700">
        {isLoading && (
          <li className="px-3 py-3 text-xs text-gray-400 dark:text-gray-500 italic">Loading…</li>
        )}
        {isError && (
          <li className="px-3 py-3 text-xs text-red-600 dark:text-red-400">
            {error instanceof ApiError ? error.message : 'Could not load files for this source.'}
          </li>
        )}
        {!isLoading && !isError && entries.length === 0 && (
          <li className="px-3 py-3 text-xs text-gray-400 dark:text-gray-500 italic">No CSV files found here.</li>
        )}
        {!isError && entries.map((entry: InputDirectoryEntry) => (
          <li key={entry.path}>
            <button
              type="button"
              onClick={() => entry.kind === 'directory' ? navigate(entry.path) : onSelect(entry.path)}
              className="w-full text-left px-3 py-2 flex items-center gap-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            >
              <FontAwesomeIcon
                icon={entry.kind === 'directory' ? faFolder : faFile}
                className={entry.kind === 'directory' ? 'text-amber-400 shrink-0' : 'text-gray-400 dark:text-gray-500 shrink-0'}
                aria-hidden="true"
              />
              <span className="truncate text-gray-900 dark:text-gray-100">{entry.name}</span>
              {entry.kind === 'file' && entry.row_count != null && (
                <span className="ml-auto shrink-0 text-xs text-gray-400 dark:text-gray-500">
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
