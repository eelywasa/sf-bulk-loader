import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Tabs } from '../../components/ui/Tabs'

const sampleTabs = [
  { id: 'overview', label: 'Overview', content: <div>Overview content</div> },
  { id: 'details', label: 'Details', content: <div>Details content</div> },
  { id: 'raw', label: 'Raw', content: <div>Raw content</div> },
]

describe('Tabs', () => {
  it('renders all tab labels', () => {
    render(<Tabs tabs={sampleTabs} />)
    expect(screen.getByRole('tab', { name: 'Overview' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Details' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Raw' })).toBeInTheDocument()
  })

  it('shows first tab content by default', () => {
    render(<Tabs tabs={sampleTabs} />)
    expect(screen.getByText('Overview content')).toBeVisible()
  })

  it('switches to clicked tab', async () => {
    const user = userEvent.setup()
    render(<Tabs tabs={sampleTabs} />)
    await user.click(screen.getByRole('tab', { name: 'Details' }))
    expect(screen.getByText('Details content')).toBeVisible()
  })

  it('calls onChange when tab is selected', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<Tabs tabs={sampleTabs} onChange={onChange} />)
    await user.click(screen.getByRole('tab', { name: 'Raw' }))
    expect(onChange).toHaveBeenCalledWith(2)
  })

  it('renders defaultIndex tab initially', () => {
    render(<Tabs tabs={sampleTabs} defaultIndex={1} />)
    expect(screen.getByText('Details content')).toBeVisible()
  })

  it('does not switch disabled tab', async () => {
    const disabledTabs = [
      { id: 'a', label: 'Tab A', content: <div>Content A</div> },
      { id: 'b', label: 'Tab B', content: <div>Content B</div>, disabled: true },
    ]
    const user = userEvent.setup()
    render(<Tabs tabs={disabledTabs} />)
    const tabB = screen.getByRole('tab', { name: 'Tab B' })
    expect(tabB).toBeDisabled()
    await user.click(tabB)
    // Still showing Tab A content
    expect(screen.getByText('Content A')).toBeVisible()
  })
})
