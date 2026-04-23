// ─── Shared form element class constants ──────────────────────────────────────
// Import from here rather than defining local INPUT_CLASS / LABEL_CLASS etc.
// All classes use design tokens so they work in both light and dark mode.

export const LABEL_CLASS = 'block text-sm font-medium text-content-secondary mb-1'

export const INPUT_CLASS =
  'w-full rounded-md border border-border-strong bg-surface-sunken text-content-primary ' +
  'px-3 py-2 text-sm placeholder:text-content-placeholder ' +
  'focus:outline-none focus:ring-2 focus:ring-border-focus focus:border-transparent ' +
  'disabled:opacity-50 disabled:cursor-not-allowed'

export const SELECT_CLASS =
  'w-full rounded-md border border-border-strong bg-surface-sunken text-content-primary ' +
  'px-3 py-2 text-sm ' +
  'focus:outline-none focus:ring-2 focus:ring-border-focus focus:border-transparent ' +
  'disabled:opacity-50 disabled:cursor-not-allowed'

export const TEXTAREA_CLASS = INPUT_CLASS + ' resize-y'

export const HELPER_TEXT_CLASS = 'mt-1 text-xs text-content-muted'

export const FIELD_CONTAINER_CLASS = 'space-y-1'

export const ERROR_TEXT_CLASS = 'mt-1 text-xs text-error-text'

export const FIELD_ERROR_OUTLINE = 'border-error-border focus:ring-error-border'

// Overlay shadow — apply to modals, toasts, and dropdown panels (not cards/rows)
// dark: variant is an intentional exception for shadow-opacity tuning across modes
export const OVERLAY_SHADOW_CLASS = 'shadow-xl shadow-black/10 dark:shadow-black/40'

// Checkbox input — use for all checkbox inputs; text-accent sets the checked-state fill colour
export const CHECKBOX_CLASS =
  'h-4 w-4 rounded border-border-strong text-accent focus:ring-border-focus'

// Button classes — use for non-`<button>` elements (e.g. `<Link>`) that need
// button styling. For actual `<button>` elements, use the `<Button>` component
// which composes these internally.
// Each BUTTON_*_CLASS bakes in the md size (px-4 py-2 text-sm) — the default
// for CTA-style links. Mix with `<Button size="sm|lg">` via the component.
export const BUTTON_BASE_CLASS =
  'inline-flex items-center justify-center rounded-md font-medium ' +
  'focus:outline-none focus:ring-2 focus:ring-offset-2 ' +
  'disabled:opacity-50 disabled:cursor-not-allowed ' +
  'transition-colors duration-150'

// Variant colour strings — shared between `<Button>` and non-button consumers
// (e.g. `<Link>`). Keep in sync with `Button.tsx` by importing these.
export const BUTTON_PRIMARY_COLORS =
  'bg-accent text-content-inverse hover:bg-accent-hover focus:ring-border-focus border border-transparent'
export const BUTTON_SECONDARY_COLORS =
  'bg-surface-raised text-content-primary border border-border-strong hover:bg-surface-hover focus:ring-border-focus'
export const BUTTON_GHOST_COLORS =
  'text-content-secondary hover:bg-surface-hover focus:ring-border-focus border border-transparent'

// Ready-to-apply full classes for non-`<button>` elements that need the default
// md button size. For dynamic sizes, use `<Button>` component.
const BUTTON_SIZE_MD = 'px-4 py-2 text-sm'
export const BUTTON_PRIMARY_CLASS = `${BUTTON_BASE_CLASS} ${BUTTON_SIZE_MD} ${BUTTON_PRIMARY_COLORS}`
export const BUTTON_SECONDARY_CLASS = `${BUTTON_BASE_CLASS} ${BUTTON_SIZE_MD} ${BUTTON_SECONDARY_COLORS}`
export const BUTTON_GHOST_CLASS = `${BUTTON_BASE_CLASS} ${BUTTON_SIZE_MD} ${BUTTON_GHOST_COLORS}`

// Alert/banner blocks — use for inline error/success/warning/info panels
const ALERT_BASE = 'rounded-md border px-4 py-3 text-sm'
export const ALERT_ERROR   = `${ALERT_BASE} bg-error-bg border-error-border text-error-text`
export const ALERT_SUCCESS = `${ALERT_BASE} bg-success-bg border-success-border text-success-text`
export const ALERT_WARNING = `${ALERT_BASE} bg-warning-bg border-warning-border text-warning-text`
export const ALERT_INFO    = `${ALERT_BASE} bg-info-bg border-info-border text-info-text`
