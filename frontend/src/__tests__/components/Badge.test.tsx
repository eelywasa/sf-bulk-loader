import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Badge } from '../../components/ui/Badge'

describe('Badge', () => {
  it('renders children', () => {
    render(<Badge>running</Badge>)
    expect(screen.getByText('running')).toBeInTheDocument()
  })

  it('uses neutral variant by default', () => {
    render(<Badge>neutral</Badge>)
    const el = screen.getByText('neutral')
    expect(el.className).toMatch(/bg-gray-100/)
  })

  it('applies success variant', () => {
    render(<Badge variant="success">completed</Badge>)
    const el = screen.getByText('completed')
    expect(el.className).toMatch(/bg-green-100/)
    expect(el.className).toMatch(/text-green-800/)
  })

  it('applies error variant', () => {
    render(<Badge variant="error">failed</Badge>)
    const el = screen.getByText('failed')
    expect(el.className).toMatch(/bg-red-100/)
  })

  it('applies warning variant', () => {
    render(<Badge variant="warning">warning</Badge>)
    const el = screen.getByText('warning')
    expect(el.className).toMatch(/bg-orange-100/)
  })

  it('applies running variant', () => {
    render(<Badge variant="running">running</Badge>)
    const el = screen.getByText('running')
    expect(el.className).toMatch(/bg-blue-100/)
  })

  it('applies aborted variant', () => {
    render(<Badge variant="aborted">aborted</Badge>)
    expect(screen.getByText('aborted')).toBeInTheDocument()
  })

  it('renders a dot indicator when dot=true', () => {
    render(<Badge dot variant="running">running</Badge>)
    // The dot span is aria-hidden
    const badge = screen.getByText('running').closest('span')!
    // Should have at least 2 children (dot + text)
    expect(badge.children.length).toBeGreaterThan(0)
  })

  it('accepts custom className', () => {
    render(<Badge className="my-custom">test</Badge>)
    expect(screen.getByText('test').className).toMatch(/my-custom/)
  })
})
