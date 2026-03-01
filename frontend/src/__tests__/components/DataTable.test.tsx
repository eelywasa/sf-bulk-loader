import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DataTable, type Column } from '../../components/ui/DataTable'

interface Row {
  id: string
  name: string
  status: string
}

const columns: Column<Row>[] = [
  { key: 'name', header: 'Name', render: (r) => r.name },
  { key: 'status', header: 'Status', render: (r) => r.status },
]

const data: Row[] = [
  { id: '1', name: 'Alice', status: 'active' },
  { id: '2', name: 'Bob', status: 'inactive' },
]

describe('DataTable', () => {
  it('renders column headers', () => {
    render(<DataTable columns={columns} data={data} keyExtractor={(r) => r.id} />)
    expect(screen.getByText('Name')).toBeInTheDocument()
    expect(screen.getByText('Status')).toBeInTheDocument()
  })

  it('renders all rows', () => {
    render(<DataTable columns={columns} data={data} keyExtractor={(r) => r.id} />)
    expect(screen.getByText('Alice')).toBeInTheDocument()
    expect(screen.getByText('Bob')).toBeInTheDocument()
  })

  it('renders empty message when data is empty', () => {
    render(
      <DataTable
        columns={columns}
        data={[]}
        keyExtractor={(r) => r.id}
        emptyMessage="No results found."
      />,
    )
    expect(screen.getByText('No results found.')).toBeInTheDocument()
  })

  it('renders default empty message', () => {
    render(<DataTable columns={columns} data={[]} keyExtractor={(r) => r.id} />)
    expect(screen.getByText('No data available.')).toBeInTheDocument()
  })

  it('shows loading spinner when loading=true', () => {
    const { container } = render(
      <DataTable columns={columns} data={[]} keyExtractor={(r) => r.id} loading />,
    )
    expect(container.querySelector('.animate-spin')).toBeInTheDocument()
    // Data should not be shown
    expect(screen.queryByText('No data available.')).not.toBeInTheDocument()
  })

  it('calls onRowClick when a row is clicked', () => {
    const onClick = vi.fn()
    render(
      <DataTable
        columns={columns}
        data={data}
        keyExtractor={(r) => r.id}
        onRowClick={onClick}
      />,
    )
    fireEvent.click(screen.getByText('Alice').closest('tr')!)
    expect(onClick).toHaveBeenCalledWith(data[0])
  })

  it('uses render function for cell content', () => {
    const customColumns: Column<Row>[] = [
      {
        key: 'name',
        header: 'Name',
        render: (r) => <span data-testid="cell">{r.name.toUpperCase()}</span>,
      },
    ]
    render(<DataTable columns={customColumns} data={data} keyExtractor={(r) => r.id} />)
    expect(screen.getAllByTestId('cell')[0]).toHaveTextContent('ALICE')
  })
})
