/**
 * Tests for MfaBackupCodesModal (SFBL-250).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import MfaBackupCodesModal from '../MfaBackupCodesModal'

const CODES = ['aaaa1-bbbb2', 'cccc3-dddd4', 'eeee5-ffff6']

describe('MfaBackupCodesModal', () => {
  let originalClipboard: typeof navigator.clipboard | undefined
  let originalCreateObjectURL: typeof URL.createObjectURL
  let originalRevokeObjectURL: typeof URL.revokeObjectURL

  beforeEach(() => {
    originalClipboard = navigator.clipboard
    originalCreateObjectURL = URL.createObjectURL
    originalRevokeObjectURL = URL.revokeObjectURL
  })

  afterEach(() => {
    if (originalClipboard !== undefined) {
      Object.defineProperty(navigator, 'clipboard', { value: originalClipboard, configurable: true })
    }
    URL.createObjectURL = originalCreateObjectURL
    URL.revokeObjectURL = originalRevokeObjectURL
    vi.restoreAllMocks()
  })

  it('renders all codes', () => {
    render(<MfaBackupCodesModal codes={CODES} onClose={() => {}} />)
    for (const c of CODES) {
      expect(screen.getByText(c)).toBeInTheDocument()
    }
  })

  it('disables Close until the acknowledgement checkbox is ticked', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<MfaBackupCodesModal codes={CODES} onClose={onClose} />)

    const closeBtn = screen.getByTestId('backup-codes-close') as HTMLButtonElement
    expect(closeBtn).toBeDisabled()

    // Click should not fire close handler while disabled
    await user.click(closeBtn)
    expect(onClose).not.toHaveBeenCalled()

    await user.click(screen.getByTestId('backup-codes-ack'))
    expect(closeBtn).not.toBeDisabled()

    await user.click(closeBtn)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('Download .txt builds a Blob with one code per line', async () => {
    const user = userEvent.setup()
    const captured: Array<{ blob: Blob }> = []
    URL.createObjectURL = vi.fn((blob: Blob) => {
      captured.push({ blob })
      return 'blob:fake-url'
    })
    URL.revokeObjectURL = vi.fn()

    render(<MfaBackupCodesModal codes={CODES} onClose={() => {}} />)

    await user.click(screen.getByTestId('backup-codes-download'))

    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
    expect(captured).toHaveLength(1)
    const blob = captured[0].blob
    expect(blob.type).toBe('text/plain;charset=utf-8')
    // jsdom's Blob lacks .text(); read via FileReader instead.
    const text = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = () => reject(reader.error)
      reader.readAsText(blob)
    })
    expect(text).toBe(CODES.join('\n') + '\n')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:fake-url')
  })

  it('Copy all writes newline-joined codes to clipboard and shows transient confirmation', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })

    render(<MfaBackupCodesModal codes={CODES} onClose={() => {}} />)

    await user.click(screen.getByTestId('backup-codes-copy'))

    expect(writeText).toHaveBeenCalledWith(CODES.join('\n'))
    await waitFor(() => {
      expect(screen.getByTestId('backup-codes-copy')).toHaveTextContent('Copied')
    })
  })
})
