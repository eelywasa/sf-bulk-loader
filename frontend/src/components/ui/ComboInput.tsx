import { useState, useRef, useEffect, useId } from 'react'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faChevronDown, faSpinner } from '@fortawesome/free-solid-svg-icons'
import clsx from 'clsx'
import { OVERLAY_SHADOW_CLASS } from './formStyles'

export interface ComboInputProps {
  id?: string
  value: string
  onChange: (value: string) => void
  options: string[]
  loading?: boolean
  placeholder?: string
  className?: string
  inputClassName?: string
  loadingMessage?: string
}

export function ComboInput({
  id,
  value,
  onChange,
  options,
  loading = false,
  placeholder,
  className,
  inputClassName,
  loadingMessage = 'Loading columns…',
}: ComboInputProps) {
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  const listId = useId()

  // Filter options by the current typed value (case-insensitive substring)
  const filtered = value
    ? options.filter((o) => o.toLowerCase().includes(value.toLowerCase()))
    : options

  // Close on outside click
  useEffect(() => {
    function handleMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
        setActiveIndex(-1)
      }
    }
    document.addEventListener('mousedown', handleMouseDown)
    return () => document.removeEventListener('mousedown', handleMouseDown)
  }, [])

  // Reset active index when options prop changes
  useEffect(() => {
    setActiveIndex(-1)
  }, [options])

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    onChange(e.target.value)
    setActiveIndex(-1)
    if (e.target.value && options.length > 0) setOpen(true)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open) {
      if (e.key === 'ArrowDown' && filtered.length > 0) {
        setOpen(true)
        setActiveIndex(0)
        e.preventDefault()
      }
      return
    }
    switch (e.key) {
      case 'ArrowDown':
        setActiveIndex((i) => Math.min(i + 1, filtered.length - 1))
        e.preventDefault()
        break
      case 'ArrowUp':
        setActiveIndex((i) => Math.max(i - 1, 0))
        e.preventDefault()
        break
      case 'Enter':
        if (activeIndex >= 0 && activeIndex < filtered.length) {
          onChange(filtered[activeIndex])
          setOpen(false)
          setActiveIndex(-1)
          e.preventDefault()
        }
        break
      case 'Escape':
        setOpen(false)
        setActiveIndex(-1)
        e.preventDefault()
        break
    }
  }

  function selectOption(option: string) {
    onChange(option)
    setOpen(false)
    setActiveIndex(-1)
  }

  const showDropdown = open && (loading || filtered.length > 0)

  return (
    <div ref={containerRef} className={clsx('relative', className)}>
      <div className="relative flex items-center">
        <input
          id={id}
          type="text"
          value={value}
          onChange={handleInputChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          autoComplete="off"
          aria-autocomplete="list"
          aria-expanded={showDropdown}
          aria-controls={showDropdown ? listId : undefined}
          aria-activedescendant={
            activeIndex >= 0 ? `${listId}-option-${activeIndex}` : undefined
          }
          className={clsx('w-full pr-8', inputClassName)}
        />
        <button
          type="button"
          tabIndex={-1}
          aria-label="Show options"
          onClick={() => {
            if (options.length > 0 || loading) setOpen((o) => !o)
          }}
          className="absolute right-2 text-content-muted hover:text-content-secondary transition-colors"
        >
          {loading ? (
            <FontAwesomeIcon icon={faSpinner} className="animate-spin text-xs" />
          ) : (
            <FontAwesomeIcon icon={faChevronDown} className="text-xs" />
          )}
        </button>
      </div>

      {showDropdown && (
        <ul
          id={listId}
          role="listbox"
          className={clsx(
            'absolute z-50 mt-1 w-full rounded-md border border-border-base bg-surface-elevated max-h-48 overflow-y-auto text-sm',
            OVERLAY_SHADOW_CLASS,
          )}
        >
          {loading ? (
            <li className="px-3 py-2 text-content-muted italic">
              {loadingMessage}
            </li>
          ) : (
            filtered.map((opt, i) => (
              <li
                key={opt}
                id={`${listId}-option-${i}`}
                role="option"
                aria-selected={value === opt}
                onMouseDown={(e) => {
                  e.preventDefault() // prevent blur before click
                  selectOption(opt)
                }}
                onMouseEnter={() => setActiveIndex(i)}
                className={clsx(
                  'px-3 py-2 cursor-pointer truncate',
                  i === activeIndex
                    ? 'bg-surface-selected text-content-selected'
                    : 'text-content-primary hover:bg-surface-hover',
                )}
              >
                {opt}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  )
}
