import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ComboInput } from '../../components/ui/ComboInput'

const OPTIONS = ['Name', 'ExternalId__c', 'BillingCity']

function renderCombo(
  props: Partial<React.ComponentProps<typeof ComboInput>> = {},
) {
  const onChange = props.onChange ?? vi.fn()
  render(
    <ComboInput
      value=""
      onChange={onChange}
      options={OPTIONS}
      placeholder="Type or pick…"
      {...props}
    />,
  )
  return { onChange }
}

describe('ComboInput', () => {
  // ── Rendering ──────────────────────────────────────────────────────────────

  it('renders the text input', () => {
    renderCombo()
    expect(screen.getByRole('textbox')).toBeInTheDocument()
  })

  it('renders the toggle button', () => {
    renderCombo()
    expect(screen.getByRole('button', { name: 'Show options' })).toBeInTheDocument()
  })

  it('does not show the listbox initially', () => {
    renderCombo()
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('shows a spinner when loading=true', () => {
    renderCombo({ loading: true, options: [] })
    // spinner is visible via the button — the listbox won't appear without options
    // but clicking the button when loading should show the loading state
    const btn = screen.getByRole('button', { name: 'Show options' })
    expect(btn.querySelector('svg')).toBeInTheDocument()
  })

  // ── Opening / closing ──────────────────────────────────────────────────────

  it('opens the listbox when toggle button is clicked', async () => {
    const user = userEvent.setup()
    renderCombo()
    await user.click(screen.getByRole('button', { name: 'Show options' }))
    expect(screen.getByRole('listbox')).toBeInTheDocument()
  })

  it('shows all options when open', async () => {
    const user = userEvent.setup()
    renderCombo()
    await user.click(screen.getByRole('button', { name: 'Show options' }))
    expect(screen.getByRole('option', { name: 'Name' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'ExternalId__c' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'BillingCity' })).toBeInTheDocument()
  })

  it('closes the listbox when Escape is pressed', async () => {
    const user = userEvent.setup()
    renderCombo()
    // Open via ArrowDown on the input so focus stays on the input
    await user.click(screen.getByRole('textbox'))
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  // ── Selection ──────────────────────────────────────────────────────────────

  it('calls onChange with the option value when an option is clicked', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    renderCombo({ onChange })
    await user.click(screen.getByRole('button', { name: 'Show options' }))
    await user.click(screen.getByRole('option', { name: 'ExternalId__c' }))
    expect(onChange).toHaveBeenCalledWith('ExternalId__c')
  })

  it('closes the listbox after selecting an option', async () => {
    const user = userEvent.setup()
    renderCombo()
    await user.click(screen.getByRole('button', { name: 'Show options' }))
    await user.click(screen.getByRole('option', { name: 'Name' }))
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  // ── Keyboard navigation ────────────────────────────────────────────────────

  it('opens and highlights first option on ArrowDown from closed state', async () => {
    const user = userEvent.setup()
    renderCombo()
    await user.click(screen.getByRole('textbox'))
    await user.keyboard('{ArrowDown}')
    const listbox = screen.getByRole('listbox')
    expect(listbox).toBeInTheDocument()
    const options = screen.getAllByRole('option')
    expect(options[0]).toHaveClass('bg-surface-selected')
  })

  it('selects highlighted option on Enter', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    renderCombo({ onChange })
    await user.click(screen.getByRole('textbox'))
    await user.keyboard('{ArrowDown}{Enter}')
    expect(onChange).toHaveBeenCalledWith('Name')
  })

  it('navigates down through options with ArrowDown', async () => {
    const user = userEvent.setup()
    renderCombo()
    await user.click(screen.getByRole('textbox'))
    await user.keyboard('{ArrowDown}{ArrowDown}')
    const options = screen.getAllByRole('option')
    expect(options[1]).toHaveClass('bg-surface-selected')
  })

  it('does not navigate above first option with ArrowUp', async () => {
    const user = userEvent.setup()
    renderCombo()
    await user.click(screen.getByRole('textbox'))
    await user.keyboard('{ArrowDown}{ArrowDown}{ArrowUp}')
    const options = screen.getAllByRole('option')
    expect(options[0]).toHaveClass('bg-surface-selected')
  })

  // ── Free text ──────────────────────────────────────────────────────────────

  it('calls onChange when user types in the input', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    renderCombo({ onChange })
    await user.type(screen.getByRole('textbox'), 'abc')
    // Controlled input calls onChange once per keystroke with the single new char
    expect(onChange).toHaveBeenCalledTimes(3)
    expect(onChange).toHaveBeenNthCalledWith(1, 'a')
    expect(onChange).toHaveBeenNthCalledWith(2, 'b')
    expect(onChange).toHaveBeenNthCalledWith(3, 'c')
  })

  // ── Loading state ──────────────────────────────────────────────────────────

  it('shows loading message when loading=true and opened', async () => {
    const user = userEvent.setup()
    renderCombo({ loading: true, options: [] })
    // Manually open by providing a fake option to trigger the button —
    // instead, render with loading=true and a non-empty array to allow open
    render(
      <ComboInput
        value=""
        onChange={vi.fn()}
        options={['placeholder']}
        loading={true}
      />,
    )
    await user.click(screen.getAllByRole('button', { name: 'Show options' })[1])
    expect(screen.getByText('Loading columns…')).toBeInTheDocument()
  })
})
