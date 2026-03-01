import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EmptyState } from '../../components/ui/EmptyState'

describe('EmptyState', () => {
  it('renders the title', () => {
    render(<EmptyState title="Nothing here yet" />)
    expect(screen.getByText('Nothing here yet')).toBeInTheDocument()
  })

  it('renders description when provided', () => {
    render(<EmptyState title="Empty" description="Add some items to get started." />)
    expect(screen.getByText('Add some items to get started.')).toBeInTheDocument()
  })

  it('does not render description when omitted', () => {
    const { container } = render(<EmptyState title="Empty" />)
    // Only one paragraph-like element should exist (the title)
    const ps = container.querySelectorAll('p')
    expect(ps).toHaveLength(0)
  })

  it('renders action when provided', () => {
    render(
      <EmptyState
        title="Empty"
        action={<button>Create item</button>}
      />,
    )
    expect(screen.getByRole('button', { name: 'Create item' })).toBeInTheDocument()
  })

  it('renders default icon when no icon prop is given', () => {
    const { container } = render(<EmptyState title="Empty" />)
    expect(container.querySelector('svg')).toBeInTheDocument()
  })

  it('renders custom icon when provided', () => {
    render(
      <EmptyState
        title="Empty"
        icon={<span data-testid="custom-icon">📁</span>}
      />,
    )
    expect(screen.getByTestId('custom-icon')).toBeInTheDocument()
  })

  it('applies custom className', () => {
    const { container } = render(
      <EmptyState title="Empty" className="my-empty" />,
    )
    expect(container.firstChild).toHaveClass('my-empty')
  })
})
