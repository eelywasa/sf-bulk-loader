import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Modal } from '../../components/ui/Modal'

describe('Modal', () => {
  it('renders content when open=true', () => {
    render(
      <Modal open onClose={() => {}}>
        <p>Modal body</p>
      </Modal>,
    )
    expect(screen.getByText('Modal body')).toBeInTheDocument()
  })

  it('does not render content when open=false', () => {
    render(
      <Modal open={false} onClose={() => {}}>
        <p>Hidden content</p>
      </Modal>,
    )
    expect(screen.queryByText('Hidden content')).not.toBeInTheDocument()
  })

  it('renders title when provided', () => {
    render(
      <Modal open onClose={() => {}} title="Confirm Delete">
        Content
      </Modal>,
    )
    expect(screen.getByText('Confirm Delete')).toBeInTheDocument()
  })

  it('renders description when provided', () => {
    render(
      <Modal open onClose={() => {}} title="Title" description="This will be permanent.">
        Content
      </Modal>,
    )
    expect(screen.getByText('This will be permanent.')).toBeInTheDocument()
  })

  it('renders footer when provided', () => {
    render(
      <Modal open onClose={() => {}} footer={<button>Confirm</button>}>
        Content
      </Modal>,
    )
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument()
  })

  it('calls onClose when backdrop is clicked', () => {
    const onClose = vi.fn()
    render(
      <Modal open onClose={onClose}>
        <p>Content</p>
      </Modal>,
    )
    // Headless UI Dialog closes on Escape key
    fireEvent.keyDown(document.activeElement ?? document.body, {
      key: 'Escape',
      code: 'Escape',
    })
    expect(onClose).toHaveBeenCalled()
  })
})
