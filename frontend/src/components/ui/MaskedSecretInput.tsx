/**
 * MaskedSecretInput — a password-style text field for is_secret settings.
 *
 * - Shows "***" placeholder when no value has been typed (keep-existing mode)
 * - Shows a toggle to reveal/hide the entered text
 * - Tooltip communicates the keep-existing behaviour to the user
 */

import { useState } from 'react'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faEye, faEyeSlash } from '@fortawesome/free-solid-svg-icons'
import { INPUT_CLASS } from './formStyles'
import clsx from 'clsx'

interface MaskedSecretInputProps {
  id?: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
  className?: string
}

export function MaskedSecretInput({
  id,
  value,
  onChange,
  placeholder = '***',
  disabled,
  className,
}: MaskedSecretInputProps) {
  const [visible, setVisible] = useState(false)

  return (
    <div className="relative flex items-center">
      <input
        id={id}
        type={visible ? 'text' : 'password'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        title="Leave blank to keep the existing value"
        autoComplete="new-password"
        className={clsx(INPUT_CLASS, 'pr-9', className)}
      />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        disabled={disabled}
        aria-label={visible ? 'Hide secret' : 'Show secret'}
        className="absolute right-2 text-content-muted hover:text-content-secondary transition-colors disabled:opacity-50"
        tabIndex={-1}
      >
        <FontAwesomeIcon icon={visible ? faEyeSlash : faEye} className="w-4 h-4" />
      </button>
    </div>
  )
}
