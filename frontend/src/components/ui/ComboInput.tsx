import { useState, useRef, useEffect, useId } from 'react'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faChevronDown, faSpinner } from '@fortawesome/free-solid-svg-icons'
import clsx from 'clsx'

export interface ComboInputProps {
  id?: string
  value: string
  onChange: (value: string) => void
  options: string[]
  loading?: boolean
  placeholder?: string
  className?: string
  inputClassName?: string
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
}: ComboInputProps) {
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  const listId = useId()

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

  // Reset active index when options change
  useEffect(() => {
    setActiveIndex(-1)
  }, [options])

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open) {
      if (e.key === 'ArrowDown' && options.length > 0) {
        setOpen(true)
        setActiveIndex(0)
        e.preventDefault()
      }
      return
    }
    switch (e.key) {
      case 'ArrowDown':
        setActiveIndex((i) => Math.min(i + 1, options.length - 1))
        e.preventDefault()
        break
      case 'ArrowUp':
        setActiveIndex((i) => Math.max(i - 1, 0))
        e.preventDefault()
        break
      case 'Enter':
        if (activeIndex >= 0 && activeIndex < options.length) {
          onChange(options[activeIndex])
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

  const showDropdown = open && (loading || options.length > 0)

  return (
    <div ref={containerRef} className={clsx('relative', className)}>
      <div className="relative flex items-center">
        <input
          id={id}
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
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
          className="absolute right-2 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
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
          className="absolute z-50 mt-1 w-full rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg max-h-48 overflow-y-auto text-sm"
        >
          {loading ? (
            <li className="px-3 py-2 text-gray-400 dark:text-gray-500 italic">
              Loading columns…
            </li>
          ) : (
            options.map((opt, i) => (
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
                    ? 'bg-blue-50 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300'
                    : 'text-gray-900 dark:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-700',
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
