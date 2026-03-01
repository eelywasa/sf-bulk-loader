import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Progress } from '../../components/ui/Progress'

describe('Progress', () => {
  it('renders a progressbar role', () => {
    render(<Progress value={50} />)
    expect(screen.getByRole('progressbar')).toBeInTheDocument()
  })

  it('sets aria-valuenow correctly', () => {
    render(<Progress value={75} />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '75')
  })

  it('clamps value above 100 to 100', () => {
    render(<Progress value={150} />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100')
  })

  it('clamps value below 0 to 0', () => {
    render(<Progress value={-10} />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')
  })

  it('sets aria-valuemin=0 and aria-valuemax=100', () => {
    render(<Progress value={50} />)
    const bar = screen.getByRole('progressbar')
    expect(bar).toHaveAttribute('aria-valuemin', '0')
    expect(bar).toHaveAttribute('aria-valuemax', '100')
  })

  it('renders label when provided', () => {
    render(<Progress value={40} label="Loading..." />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('renders percentage when showValue=true', () => {
    render(<Progress value={65} showValue />)
    expect(screen.getByText('65%')).toBeInTheDocument()
  })

  it('sets correct inner bar width style', () => {
    const { container } = render(<Progress value={30} />)
    const bar = container.querySelector('[style]')
    expect(bar).toHaveStyle({ width: '30%' })
  })
})
