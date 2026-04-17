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

// Alert/banner blocks — use for inline error/success/warning/info panels
const ALERT_BASE = 'rounded-md border px-4 py-3 text-sm'
export const ALERT_ERROR   = `${ALERT_BASE} bg-error-bg border-error-border text-error-text`
export const ALERT_SUCCESS = `${ALERT_BASE} bg-success-bg border-success-border text-success-text`
export const ALERT_WARNING = `${ALERT_BASE} bg-warning-bg border-warning-border text-warning-text`
export const ALERT_INFO    = `${ALERT_BASE} bg-info-bg border-info-border text-info-text`
