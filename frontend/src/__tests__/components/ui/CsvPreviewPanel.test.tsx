import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { CsvPreviewPanel, type CsvPreviewPanelProps } from '../../../components/ui/CsvPreviewPanel'
import type { CsvPageResult } from '../../../api/types'

// ── Helpers ─────────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
}

function renderPanel(props: CsvPreviewPanelProps, queryClient?: QueryClient) {
  const qc = queryClient ?? makeQueryClient()
  const result = render(
    <QueryClientProvider client={qc}>
      <CsvPreviewPanel {...props} />
    </QueryClientProvider>
  )
  return { ...result, queryClient: qc }
}

function makePage(
  header: string[],
  rows: Record<string, string>[],
  hasNext: boolean,
  extra?: Partial<CsvPageResult>
): CsvPageResult {
  return {
    header,
    rows,
    has_next: hasNext,
    total_rows: extra?.total_rows ?? null,
    filtered_rows: extra?.filtered_rows ?? null,
    offset: extra?.offset ?? 0,
    limit: extra?.limit ?? 50,
    ...extra,
  }
}

const HEADER = ['Id', 'Name', 'Status']

function makeRows(count: number): Record<string, string>[] {
  return Array.from({ length: count }, (_, i) => ({
    Id: `id-${i + 1}`,
    Name: `Row ${i + 1}`,
    Status: i % 2 === 0 ? 'active' : 'inactive',
  }))
}

// ── Rendering ────────────────────────────────────────────────────────────────

describe('CsvPreviewPanel', () => {
  describe('rendering', () => {
    it('renders header columns and first page of rows', async () => {
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(2), false))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      expect(screen.getByText('Id')).toBeInTheDocument()
      expect(screen.getByText('Name')).toBeInTheDocument()
      expect(screen.getByText('Status')).toBeInTheDocument()
      expect(screen.getByText('Row 2')).toBeInTheDocument()
    })

    it('shows filename label when filename prop provided', async () => {
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(1), false))
      renderPanel({ queryKey: ['test'], fetchPage, filename: 'accounts.csv' })

      await waitFor(() => expect(screen.getByText('accounts.csv')).toBeInTheDocument())
    })
  })

  // ── Pagination ─────────────────────────────────────────────────────────────

  describe('pagination', () => {
    it('Next button advances to page 2', async () => {
      const user = userEvent.setup()
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(50), true))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      await user.click(screen.getByRole('button', { name: 'Next page' }))

      await waitFor(() =>
        expect(fetchPage).toHaveBeenCalledWith({ offset: 50, limit: 50, filters: [] })
      )
    })

    it('Prev button retreats from page 2 to page 1', async () => {
      const user = userEvent.setup()
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(50), true))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      await user.click(screen.getByRole('button', { name: 'Next page' }))
      await waitFor(() =>
        expect(fetchPage).toHaveBeenCalledWith({ offset: 50, limit: 50, filters: [] })
      )

      await user.click(screen.getByRole('button', { name: 'Previous page' }))
      await waitFor(() =>
        expect(fetchPage).toHaveBeenLastCalledWith({ offset: 0, limit: 50, filters: [] })
      )
    })

    it('First button jumps to page 1', async () => {
      const user = userEvent.setup()
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(50), true))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      await user.click(screen.getByRole('button', { name: 'Next page' }))
      await waitFor(() =>
        expect(fetchPage).toHaveBeenCalledWith({ offset: 50, limit: 50, filters: [] })
      )

      await user.click(screen.getByRole('button', { name: 'First page' }))
      await waitFor(() =>
        expect(fetchPage).toHaveBeenLastCalledWith({ offset: 0, limit: 50, filters: [] })
      )
    })

    it('Last button jumps to last page', async () => {
      const user = userEvent.setup()
      // 150 rows, pageSize 50 → totalPages = 3
      const fetchPage = vi
        .fn()
        .mockResolvedValue(makePage(HEADER, makeRows(50), true, { total_rows: 150 }))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Page 1 of 3 (150 rows)')).toBeInTheDocument())
      await user.click(screen.getByRole('button', { name: 'Last page' }))

      await waitFor(() =>
        expect(fetchPage).toHaveBeenLastCalledWith({ offset: 100, limit: 50, filters: [] })
      )
    })

    it('Next is disabled when has_next is false', async () => {
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(3), false))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    })

    it('Last button not rendered when total_rows is null', async () => {
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(50), true))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      expect(screen.queryByRole('button', { name: 'Last page' })).not.toBeInTheDocument()
    })

    it('page size selector change resets to page 1', async () => {
      const user = userEvent.setup()
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(50), true))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      // Navigate to page 2
      await user.click(screen.getByRole('button', { name: 'Next page' }))
      await waitFor(() =>
        expect(fetchPage).toHaveBeenCalledWith({ offset: 50, limit: 50, filters: [] })
      )

      // Change page size → should reset to page 1
      await user.selectOptions(screen.getByRole('combobox', { name: 'Page size' }), '25')
      await waitFor(() =>
        expect(fetchPage).toHaveBeenLastCalledWith({ offset: 0, limit: 25, filters: [] })
      )
    })
  })

  // ── Filters ────────────────────────────────────────────────────────────────

  describe('filters', () => {
    it('Add Filter appends a filter row with column select', async () => {
      const user = userEvent.setup()
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(2), false))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      await user.click(screen.getByText('+ Add Filter'))

      expect(screen.getByRole('combobox', { name: 'Filter column' })).toBeInTheDocument()
      expect(screen.getByRole('textbox', { name: 'Filter value' })).toBeInTheDocument()
    })

    it('Apply triggers fetchPage with activeFilters', async () => {
      const user = userEvent.setup()
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(2), false))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      await user.click(screen.getByText('+ Add Filter'))

      await user.selectOptions(screen.getByRole('combobox', { name: 'Filter column' }), 'Name')
      await user.type(screen.getByRole('textbox', { name: 'Filter value' }), 'Row 1')
      await user.click(screen.getByRole('button', { name: 'Apply' }))

      await waitFor(() =>
        expect(fetchPage).toHaveBeenCalledWith({
          offset: 0,
          limit: 50,
          filters: [{ column: 'Name', value: 'Row 1' }],
        })
      )
    })

    it('Apply is disabled while filter row column is empty', async () => {
      const user = userEvent.setup()
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(2), false))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      await user.click(screen.getByText('+ Add Filter'))
      // column still empty — type a value but leave column unselected
      await user.type(screen.getByRole('textbox', { name: 'Filter value' }), 'foo')

      expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
    })

    it('Apply is disabled while filter row value is empty', async () => {
      const user = userEvent.setup()
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(2), false))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      await user.click(screen.getByText('+ Add Filter'))
      // Select a column but leave value empty
      await user.selectOptions(screen.getByRole('combobox', { name: 'Filter column' }), 'Name')

      expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
    })

    it('Clear Filters resets to page 1 with empty filters', async () => {
      const user = userEvent.setup()
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(2), false))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      // Apply a filter
      await user.click(screen.getByText('+ Add Filter'))
      await user.selectOptions(screen.getByRole('combobox', { name: 'Filter column' }), 'Name')
      await user.type(screen.getByRole('textbox', { name: 'Filter value' }), 'foo')
      await user.click(screen.getByRole('button', { name: 'Apply' }))
      await waitFor(() =>
        expect(fetchPage).toHaveBeenCalledWith({
          offset: 0,
          limit: 50,
          filters: [{ column: 'Name', value: 'foo' }],
        })
      )

      // Clear Filters
      await user.click(screen.getByText('Clear Filters'))
      await waitFor(() =>
        expect(fetchPage).toHaveBeenLastCalledWith({ offset: 0, limit: 50, filters: [] })
      )
    })

    it('column used in one filter row is excluded from another row options', async () => {
      const user = userEvent.setup()
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(2), false))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      // Add first filter row and select "Name"
      await user.click(screen.getByText('+ Add Filter'))
      const columnSelects = screen.getAllByRole('combobox', { name: 'Filter column' })
      await user.selectOptions(columnSelects[0], 'Name')

      // Add second filter row
      await user.click(screen.getByText('+ Add Filter'))
      const secondSelect = screen.getAllByRole('combobox', { name: 'Filter column' })[1]

      // "Name" should not appear in the second row's options
      const options = Array.from(secondSelect.querySelectorAll('option')).map((o) => o.textContent)
      expect(options).not.toContain('Name')
      expect(options).toContain('Id')
      expect(options).toContain('Status')
    })
  })

  // ── States ─────────────────────────────────────────────────────────────────

  describe('loading / error / empty states', () => {
    it('shows spinner overlay while fetching', async () => {
      let resolve!: (v: CsvPageResult) => void
      const deferred = new Promise<CsvPageResult>((res) => {
        resolve = res
      })
      const fetchPage = vi.fn().mockReturnValue(deferred)
      renderPanel({ queryKey: ['test'], fetchPage })

      // Spinner should be visible during first load
      expect(screen.getByLabelText('Loading')).toBeInTheDocument()

      // Resolve and assert spinner disappears
      resolve(makePage(HEADER, makeRows(2), false))
      await waitFor(() =>
        expect(screen.queryByLabelText('Loading')).not.toBeInTheDocument()
      )
    })

    it('shows inline error message on fetch failure', async () => {
      const fetchPage = vi.fn().mockRejectedValue(new Error('Server error'))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
      expect(screen.getByRole('alert')).toHaveTextContent('Server error')
    })

    it('shows empty state with filter message when rows empty and filters active', async () => {
      const user = userEvent.setup()
      // First call: return rows so filters can be applied; second: return empty (filtered)
      const fetchPage = vi
        .fn()
        .mockResolvedValueOnce(makePage(HEADER, makeRows(2), false))
        .mockResolvedValueOnce(makePage(HEADER, [], false, { filtered_rows: 0 }))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      await user.click(screen.getByText('+ Add Filter'))
      await user.selectOptions(screen.getByRole('combobox', { name: 'Filter column' }), 'Name')
      await user.type(screen.getByRole('textbox', { name: 'Filter value' }), 'zzz')
      await user.click(screen.getByRole('button', { name: 'Apply' }))

      await waitFor(() =>
        expect(
          screen.getByText('No rows match the current filters')
        ).toBeInTheDocument()
      )
    })

    it('shows empty state with no-data message when rows empty and no filters', async () => {
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, [], false))
      renderPanel({ queryKey: ['test'], fetchPage })

      await waitFor(() =>
        expect(screen.getByText('This file contains no data rows')).toBeInTheDocument()
      )
    })
  })

  // ── Reset on queryKey change ───────────────────────────────────────────────

  describe('queryKey change', () => {
    it('resets page and filters when queryKey changes', async () => {
      const user = userEvent.setup()
      const fetchPage = vi.fn().mockResolvedValue(makePage(HEADER, makeRows(50), true))
      const queryClient = makeQueryClient()
      const { rerender } = renderPanel({ queryKey: ['file1'], fetchPage }, queryClient)

      await waitFor(() => expect(screen.getByText('Row 1')).toBeInTheDocument())
      // Navigate to page 2
      await user.click(screen.getByRole('button', { name: 'Next page' }))
      await waitFor(() =>
        expect(fetchPage).toHaveBeenCalledWith({ offset: 50, limit: 50, filters: [] })
      )

      // Switch to a different file — should reset to page 1
      rerender(
        <QueryClientProvider client={queryClient}>
          <CsvPreviewPanel queryKey={['file2']} fetchPage={fetchPage} />
        </QueryClientProvider>
      )

      await waitFor(() =>
        expect(fetchPage).toHaveBeenLastCalledWith({ offset: 0, limit: 50, filters: [] })
      )
    })
  })
})
