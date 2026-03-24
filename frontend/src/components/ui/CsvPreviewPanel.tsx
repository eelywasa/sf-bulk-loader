import React, { useState, useEffect } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { EmptyState } from './EmptyState'
import type { FilterRule, CsvFetchParams, CsvPageResult } from '../../api/types'

export interface CsvPreviewPanelProps {
  queryKey: unknown[]
  fetchPage: (params: CsvFetchParams) => Promise<CsvPageResult>
  filename?: string
}

const PAGE_SIZE_OPTIONS = [25, 50, 100, 250]

export function CsvPreviewPanel({ queryKey, fetchPage, filename }: CsvPreviewPanelProps) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [draftFilters, setDraftFilters] = useState<FilterRule[]>([])
  const [activeFilters, setActiveFilters] = useState<FilterRule[]>([])

  // Reset state when the base queryKey changes (e.g. new file selected)
  const baseKey = JSON.stringify(queryKey)
  useEffect(() => {
    setPage(1)
    setDraftFilters([])
    setActiveFilters([])
  }, [baseKey])

  const offset = (page - 1) * pageSize
  const { data, isFetching, isError, error } = useQuery({
    queryKey: [...queryKey, page, pageSize, activeFilters],
    queryFn: () => fetchPage({ offset, limit: pageSize, filters: activeFilters }),
    placeholderData: keepPreviousData,
  })

  const header = data?.header ?? []
  const rows = data?.rows ?? []
  const hasNext = data?.has_next ?? false
  const filtersActive = activeFilters.length > 0
  const totalForDisplay = filtersActive ? data?.filtered_rows : data?.total_rows
  const totalPages =
    totalForDisplay != null ? Math.ceil(totalForDisplay / pageSize) : null

  // Apply button is enabled only when all draft filters are complete and no duplicate columns
  const canApply =
    draftFilters.length > 0 &&
    draftFilters.every((f) => f.column !== '' && f.value !== '') &&
    new Set(draftFilters.map((f) => f.column)).size === draftFilters.length

  function addFilter() {
    setDraftFilters((prev) => [...prev, { column: '', value: '' }])
  }

  function removeFilter(idx: number) {
    setDraftFilters((prev) => prev.filter((_, i) => i !== idx))
  }

  function updateFilter(idx: number, field: keyof FilterRule, value: string) {
    setDraftFilters((prev) =>
      prev.map((f, i) => (i === idx ? { ...f, [field]: value } : f))
    )
  }

  function applyFilters() {
    setActiveFilters([...draftFilters])
    setPage(1)
  }

  function clearFilters() {
    setDraftFilters([])
    setActiveFilters([])
    setPage(1)
  }

  return (
    <div className="flex flex-col gap-3">
      {filename && (
        <div className="text-sm font-medium text-content-secondary">{filename}</div>
      )}

      {/* Filter bar */}
      <div className="flex flex-col gap-2">
        {draftFilters.map((filter, idx) => {
          const otherUsed = new Set(
            draftFilters.filter((_, i) => i !== idx).map((f) => f.column).filter(Boolean)
          )
          const availableColumns = header.filter(
            (col) => !otherUsed.has(col) || col === filter.column
          )
          return (
            <div key={idx} className="flex items-center gap-2">
              <select
                value={filter.column}
                onChange={(e) => updateFilter(idx, 'column', e.target.value)}
                className="rounded border border-border-strong bg-surface-sunken text-content-primary text-sm px-2 py-1"
                aria-label="Filter column"
              >
                <option value="">Select column</option>
                {availableColumns.map((col) => (
                  <option key={col} value={col}>
                    {col}
                  </option>
                ))}
              </select>
              <span className="text-sm text-content-muted">contains</span>
              <input
                type="text"
                value={filter.value}
                onChange={(e) => updateFilter(idx, 'value', e.target.value)}
                placeholder="Filter value"
                className="rounded border border-border-strong bg-surface-sunken text-content-primary text-sm px-2 py-1 flex-1"
                aria-label="Filter value"
              />
              <button
                onClick={() => removeFilter(idx)}
                className="text-content-muted hover:text-content-secondary text-sm"
                aria-label="Remove filter"
              >
                ×
              </button>
            </div>
          )
        })}
        <div className="flex items-center gap-2">
          <button
            onClick={addFilter}
            className="text-sm text-content-link hover:underline"
          >
            + Add Filter
          </button>
          {draftFilters.length > 0 && (
            <button
              onClick={applyFilters}
              disabled={!canApply}
              className="text-sm px-3 py-1 rounded bg-blue-600 text-white disabled:opacity-50 disabled:cursor-not-allowed hover:bg-blue-700"
            >
              Apply
            </button>
          )}
          {activeFilters.length > 0 && (
            <button
              onClick={clearFilters}
              className="text-sm text-content-muted hover:underline"
            >
              Clear Filters
            </button>
          )}
        </div>
      </div>

      {/* Error state */}
      {isError && (
        <div className="text-sm text-error-text" role="alert">
          {error instanceof Error ? error.message : 'Failed to load preview'}
        </div>
      )}

      {/* Table area */}
      <div className="overflow-x-auto relative">
        {isFetching && (
          <div
            className="absolute inset-0 flex items-center justify-center bg-surface-raised/60 z-10"
            aria-label="Loading"
          >
            <span className="inline-block h-5 w-5 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
          </div>
        )}
        {rows.length === 0 && !isFetching ? (
          <EmptyState
            title={
              filtersActive
                ? 'No rows match the current filters'
                : 'This file contains no data rows'
            }
          />
        ) : (
          <table className="min-w-full divide-y divide-border-base">
            <thead className="bg-surface-sunken">
              <tr>
                {header.map((col) => (
                  <th
                    key={col}
                    scope="col"
                    className="px-4 py-2 text-left text-xs font-medium text-content-muted uppercase tracking-wider whitespace-nowrap"
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="bg-surface-raised divide-y divide-border-subtle">
              {rows.map((row, rowIdx) => (
                <tr
                  key={rowIdx}
                  className={rowIdx % 2 === 0 ? '' : 'bg-surface-hover'}
                >
                  {header.map((col) => (
                    <td
                      key={col}
                      className="px-4 py-2 text-sm text-content-primary whitespace-nowrap"
                    >
                      {row[col] ?? ''}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination controls */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-1">
          <button
            onClick={() => setPage(1)}
            disabled={page === 1}
            className="px-2 py-1 text-sm rounded border border-border-strong text-content-secondary disabled:opacity-40"
            aria-label="First page"
          >
            ← First
          </button>
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-2 py-1 text-sm rounded border border-border-strong text-content-secondary disabled:opacity-40"
            aria-label="Previous page"
          >
            ‹ Prev
          </button>
          <span className="px-2 text-sm text-content-secondary">
            {totalPages != null
              ? `Page ${page} of ${totalPages}${totalForDisplay != null ? ` (${totalForDisplay} rows)` : ''}`
              : `Page ${page}`}
          </span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={!hasNext}
            className="px-2 py-1 text-sm rounded border border-border-strong text-content-secondary disabled:opacity-40"
            aria-label="Next page"
          >
            Next ›
          </button>
          {totalPages != null && (
            <button
              onClick={() => setPage(totalPages)}
              disabled={page === totalPages}
              className="px-2 py-1 text-sm rounded border border-border-strong text-content-secondary disabled:opacity-40"
              aria-label="Last page"
            >
              Last →
            </button>
          )}
        </div>
        <div className="flex items-center gap-1">
          <label className="text-sm text-content-muted">Page size:</label>
          <select
            value={pageSize}
            onChange={(e) => {
              setPageSize(Number(e.target.value))
              setPage(1)
            }}
            className="text-sm rounded border border-border-strong bg-surface-sunken text-content-primary px-2 py-1"
            aria-label="Page size"
          >
            {PAGE_SIZE_OPTIONS.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  )
}
