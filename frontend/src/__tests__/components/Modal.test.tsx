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

  it('calls onClose when Escape is pressed', () => {
    const onClose = vi.fn()
    render(
      <Modal open onClose={onClose}>
        <p>Content</p>
      </Modal>,
    )
    fireEvent.keyDown(document.activeElement ?? document.body, {
      key: 'Escape',
      code: 'Escape',
    })
    expect(onClose).toHaveBeenCalled()
  })

  it('does not call onClose when Escape is pressed and closeOnBackdropClick={false}', () => {
    // closeOnBackdropClick=false passes a no-op to Dialog's onClose, which handles
    // both Escape and outside-clicks in Headless UI — neither should close the modal
    const onClose = vi.fn()
    render(
      <Modal open onClose={onClose} closeOnBackdropClick={false}>
        <p>Content</p>
      </Modal>,
    )
    fireEvent.keyDown(document.activeElement ?? document.body, {
      key: 'Escape',
      code: 'Escape',
    })
    expect(onClose).not.toHaveBeenCalled()
  })

  // ── SFBL-405: a tall modal must stay usable on a short viewport ────────────
  //
  // The real proof is geometric and lives in the Tier 1a spec
  // (tests/e2e/app/playwright/tier-1a/step-object-name.spec.ts), which drives
  // the step editor at CI's 1280x720 viewport and clicks the footer button.
  // jsdom has no layout, so it cannot measure anything — what it CAN pin is the
  // class contract that produces the layout. These three assertions fail the
  // moment someone removes a piece of it.

  it('caps the panel height and lays it out as a flex column', () => {
    // Without max-h-full the panel grows past a short viewport, and since
    // neither the container nor the page scrolls, the footer becomes
    // unreachable — the step editor could not be saved below ~830px.
    render(
      <Modal open onClose={() => {}} title="T" footer={<button>Save</button>}>
        <p>Body</p>
      </Modal>,
    )
    const panel = document.querySelector('.bg-surface-elevated')!
    expect(panel).toBeTruthy()
    expect(panel.className).toContain('max-h-full')
    expect(panel.className).toContain('flex-col')
  })

  it('makes the body the scrolling region, and lets it shrink', () => {
    render(
      <Modal open onClose={() => {}} title="T" footer={<button>Save</button>}>
        <p>Body</p>
      </Modal>,
    )
    const body = screen.getByText('Body').parentElement!
    expect(body.className).toContain('overflow-y-auto')
    // min-h-0 is load-bearing: a flex child defaults to min-height:auto and
    // refuses to shrink below its content, which reintroduces the overflow.
    expect(body.className).toContain('min-h-0')
  })

  it('keeps header and footer out of the scrolling region', () => {
    render(
      <Modal open onClose={() => {}} title="Edit Step" footer={<button>Save</button>}>
        <p>Body</p>
      </Modal>,
    )
    const header = screen.getByText('Edit Step').parentElement!
    const footer = screen.getByRole('button', { name: 'Save' }).parentElement!
    expect(header.className).toContain('shrink-0')
    expect(footer.className).toContain('shrink-0')
    expect(footer.className).not.toContain('overflow-y-auto')
  })
})
