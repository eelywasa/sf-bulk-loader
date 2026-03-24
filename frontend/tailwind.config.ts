import type { Config } from 'tailwindcss'

export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: '#0070d2',
          dark: '#005fb2',
          light: '#e8f4fd',
        },
        surface: {
          base:     'var(--color-surface-base)',
          raised:   'var(--color-surface-raised)',
          elevated: 'var(--color-surface-elevated)',
          overlay:  'var(--color-surface-overlay)',
          sunken:   'var(--color-surface-sunken)',
          hover:    'var(--color-surface-hover)',
          active:   'var(--color-surface-active)',
          selected: 'var(--color-surface-selected)',
        },
        content: {
          primary:     'var(--color-content-primary)',
          secondary:   'var(--color-content-secondary)',
          muted:       'var(--color-content-muted)',
          placeholder: 'var(--color-content-placeholder)',
          disabled:    'var(--color-content-disabled)',
          inverse:     'var(--color-content-inverse)',
          link:        'var(--color-content-link)',
          selected:    'var(--color-content-selected)',
        },
        border: {
          subtle: 'var(--color-border-subtle)',
          base:   'var(--color-border-base)',
          strong: 'var(--color-border-strong)',
          focus:  'var(--color-border-focus)',
        },
        accent: {
          DEFAULT: 'var(--color-accent)',
          hover:   'var(--color-accent-hover)',
          soft:    'var(--color-accent-soft)',
        },
        error: {
          bg:     'var(--color-error-bg)',
          border: 'var(--color-error-border)',
          text:   'var(--color-error-text)',
        },
        success: {
          bg:     'var(--color-success-bg)',
          border: 'var(--color-success-border)',
          text:   'var(--color-success-text)',
        },
        warning: {
          bg:     'var(--color-warning-bg)',
          border: 'var(--color-warning-border)',
          text:   'var(--color-warning-text)',
        },
        info: {
          bg:     'var(--color-info-bg)',
          border: 'var(--color-info-border)',
          text:   'var(--color-info-text)',
        },
      },
    },
  },
  plugins: [],
} satisfies Config
