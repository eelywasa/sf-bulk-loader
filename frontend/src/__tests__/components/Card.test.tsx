import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Card } from '../../components/ui/Card'

describe('Card', () => {
  it('renders children', () => {
    render(<Card>Card content</Card>)
    expect(screen.getByText('Card content')).toBeInTheDocument()
  })

  it('renders title when provided', () => {
    render(<Card title="My Card">Content</Card>)
    expect(screen.getByText('My Card')).toBeInTheDocument()
  })

  it('renders subtitle when provided', () => {
    render(<Card title="Title" subtitle="Subtitle text">Content</Card>)
    expect(screen.getByText('Subtitle text')).toBeInTheDocument()
  })

  it('does not render header when no title or actions', () => {
    const { container } = render(<Card>Content</Card>)
    // No border-b element expected in header area
    const borderEl = container.querySelector('.border-b')
    expect(borderEl).toBeNull()
  })

  it('renders header when title is provided', () => {
    const { container } = render(<Card title="Title">Content</Card>)
    const header = container.querySelector('.border-b')
    expect(header).toBeInTheDocument()
  })

  it('renders actions in the header', () => {
    render(
      <Card title="Title" actions={<button>Edit</button>}>
        Content
      </Card>,
    )
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument()
  })

  it('applies custom className', () => {
    const { container } = render(<Card className="my-card">Content</Card>)
    expect(container.firstChild).toHaveClass('my-card')
  })
})
