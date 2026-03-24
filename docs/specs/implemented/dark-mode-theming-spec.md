# Spec: Dark Mode and Theming Refactor

## Overview

The application has a working light/dark/system theme toggle (AppShell settings menu) and
Tailwind's `darkMode: 'class'` is already configured, but dark-mode coverage is incomplete
and inconsistent. Some components (AppShell, Login, FilePicker, CsvPreviewPanel) are
well-themed; the majority of UI components and all data pages carry hard-coded light-only
Tailwind classes.

This spec defines a systematic refactor to produce a coherent, fully-themed interface across
both modes. It is split into ten tickets that can be executed sequentially by an agent, each
building on the previous.

---

## Design Decisions

| Topic | Decision |
|---|---|
| Token architecture | CSS custom properties (design tokens) defined in `index.css`, mapped to Tailwind utilities via `tailwind.config.ts` |
| Token naming in Tailwind | Custom colour palette keys under `surface`, `content`, `border` namespaces (e.g. `bg-surface-raised`, `text-content-primary`) |
| Shadow policy — elevation | Dark mode elevation expressed through surface colour steps and border contrast, not shadows |
| Shadow policy — overlays | Modals and dropdowns retain a shadow in both modes, using reduced opacity and blur in dark mode (`dark:shadow-black/40`) |
| Form element styling | Consolidated into `src/components/ui/formStyles.ts` — shared constants imported by all pages and components |
| Semantic state colours | Error/success/warning/info tokens defined in the token system; each has a bg, border, and text token pair |
| Accent colour policy | Introduce semantic accent tokens for links, primary actions, and selected states; temporary raw Tailwind blues are allowed only where the spec explicitly permits them |
| Migration approach | Token system first, then UI components, then pages — each ticket is independently verifiable |

---

## Token Architecture (reference for all tickets)

### CSS custom properties (`src/index.css`)

Properties are defined on `:root` (light) and overridden in `html.dark {}`. Values must
use the `theme()` helper so Tailwind processes them at build time.

#### Surface tokens
| Token | Light | Dark | Purpose |
|---|---|---|---|
| `--color-surface-base` | gray-50 | gray-950 | Page background |
| `--color-surface-raised` | white | gray-900 | Cards, panels, table bodies |
| `--color-surface-elevated` | white | gray-800 | Modals, dropdown panels |
| `--color-surface-overlay` | white | gray-800 | Overlay surfaces that need explicit elevation semantics (toast, popover, modal variants) |
| `--color-surface-sunken` | gray-100 | gray-900 | Input fields, code blocks, thead |
| `--color-surface-hover` | gray-50 | gray-800 | Row/item hover |
| `--color-surface-active` | gray-100 | gray-700 | Pressed state |
| `--color-surface-selected` | blue-50 | blue-950 | Persistent selected rows/items that should not rely on generic active styling |

#### Content (text) tokens
| Token | Light | Dark | Purpose |
|---|---|---|---|
| `--color-content-primary` | gray-900 | gray-100 | Headings, primary values |
| `--color-content-secondary` | gray-600 | gray-300 | Secondary labels |
| `--color-content-muted` | gray-500 | gray-400 | Captions and metadata |
| `--color-content-placeholder` | gray-500 | gray-400 | Placeholder text inside form fields |
| `--color-content-disabled` | gray-400 | gray-600 | Disabled controls |
| `--color-content-inverse` | white | gray-900 | Text on solid-colour backgrounds |
| `--color-content-link` | blue-600 | blue-400 | Anchor and link text |
| `--color-content-selected` | blue-700 | blue-300 | Text/icons inside selected items |

#### Border tokens
| Token | Light | Dark | Purpose |
|---|---|---|---|
| `--color-border-subtle` | gray-100 | gray-800 | Very soft separators where `border-base` is too strong |
| `--color-border-base` | gray-200 | gray-700 | Card borders, dividers, table lines |
| `--color-border-strong` | gray-300 | gray-600 | Input borders, strong separators |
| `--color-border-focus` | blue-500 | blue-400 | Focus rings |

#### State tokens (error / success / warning / info)
Each state has three tokens: `bg`, `border`, `text`.

| Token | Light | Dark |
|---|---|---|
| `--color-error-bg` | red-50 | red-950 |
| `--color-error-border` | red-200 | red-800 |
| `--color-error-text` | red-700 | red-400 |
| `--color-success-bg` | green-50 | green-950 |
| `--color-success-border` | green-200 | green-800 |
| `--color-success-text` | green-700 | green-400 |
| `--color-warning-bg` | amber-50 | amber-950 |
| `--color-warning-border` | amber-200 | amber-800 |
| `--color-warning-text` | amber-700 | amber-400 |
| `--color-info-bg` | blue-50 | blue-950 |
| `--color-info-border` | blue-200 | blue-800 |
| `--color-info-text` | blue-700 | blue-400 |

#### Accent tokens
| Token | Light | Dark | Purpose |
|---|---|---|---|
| `--color-accent` | blue-600 | blue-400 | Primary action and accent colour |
| `--color-accent-hover` | blue-700 | blue-300 | Hover state for accent surfaces/text |
| `--color-accent-soft` | blue-50 | blue-950 | Soft accent backgrounds and selections |

### Tailwind config extension

The custom properties are exposed as Tailwind colour utilities in `tailwind.config.ts`:

```ts
theme: {
  extend: {
    colors: {
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
        bg: 'var(--color-error-bg)', border: 'var(--color-error-border)', text: 'var(--color-error-text)',
      },
      success: {
        bg: 'var(--color-success-bg)', border: 'var(--color-success-border)', text: 'var(--color-success-text)',
      },
      warning: {
        bg: 'var(--color-warning-bg)', border: 'var(--color-warning-border)', text: 'var(--color-warning-text)',
      },
      info: {
        bg: 'var(--color-info-bg)', border: 'var(--color-info-border)', text: 'var(--color-info-text)',
      },
    },
  },
}
```

This lets components write `bg-surface-raised text-content-primary border-border-base` — one
class set that works in both modes without any ordinary colour `dark:` variants in component markup.

### Guardrails for token usage
- No component or page should introduce raw gray/white colour classes for ordinary theming once the token system exists.
- Temporary raw Tailwind palette colours are allowed only for accent/fill cases explicitly permitted by this spec (for example, some primary buttons or progress fills).
- If a raw accent colour starts being reused broadly, add a semantic accent token instead of spreading more raw palette classes.
- Exceptional `dark:` usage is allowed only for non-token concerns such as overlay shadow tuning, browser-control quirks, or third-party component patching. These exceptions should be rare and commented inline.

### Existing dark: variants in already-themed files

`AppShell.tsx`, `Login.tsx`, `FilePicker.tsx`, `CsvPreviewPanel.tsx`, and `ComboInput.tsx`
already carry explicit `dark:` variants. These files should be **migrated to token classes** as
part of the relevant ticket, removing the `dark:` variants so the token system becomes the single
source of truth. This is not optional — dual-maintaining inline dark variants and tokens will
drift. Assign ownership explicitly during implementation:
- `AppShell.tsx` → container/shell work alongside Ticket 4 or a dedicated shell cleanup subtask
- `ComboInput.tsx` → Ticket 2 shared-form-style adoption or Ticket 3 atomic input cleanup
- `Login.tsx`, `FilePicker.tsx`, `CsvPreviewPanel.tsx` → whichever ticket owns the route/component that renders them; do not leave them as implied cleanup items

---

## Ticket 1 — Design Token Foundation — ✅ DONE

### Goal
Establish the CSS custom property system and Tailwind config extension. No component changes.
Subsequent tickets depend on this being complete and correct.

### Affected files
| File | Change |
|---|---|
| `frontend/src/index.css` | Define all tokens in `:root` and `html.dark {}` |
| `frontend/tailwind.config.ts` | Extend `theme.colors` with token references |

### Implementation notes
- Use `theme()` helper in CSS values so Tailwind colour values are resolved at build time (e.g.
  `theme('colors.gray.50')`). Verify this is supported by the project's PostCSS setup. If it is not, fall back to literal hex/OKLCH values derived from the current Tailwind palette rather than blocking the refactor.
- The `html.dark` block overrides every token defined in `:root` — no token should be left
  without a dark override.
- Add a short comment block grouping tokens by semantic purpose (surface/content/border/state/accent) so future edits stay disciplined.
- The base layer must also set `background-color` and `color` on `body` using the token values:
  ```css
  body {
    @apply bg-surface-base text-content-primary;
  }
  ```
  This replaces the current `bg-gray-50 text-gray-900` which only works in light mode.
- Do **not** remove any existing `dark:` variants from components in this ticket — that happens
  in later tickets.
- After applying changes, run `npm run typecheck` and `npm run build` to confirm no Tailwind
  compilation errors.

### Acceptance criteria
- All tokens listed in the reference table above are defined in `index.css`.
- `tailwind.config.ts` exposes `bg-surface-*`, `text-content-*`, `border-border-*`, and state
  token classes.
- `npm run build` passes with no errors.
- Toggling dark mode in the browser visibly changes the page background colour (confirming
  token switching works end-to-end).

---

## Ticket 2 — Shared Form Styles — ✅ DONE

### Goal
Create a single shared constants file for all form element styling. Replace scattered inline
class strings in pages and components.

### Affected files
| File | Change |
|---|---|
| `frontend/src/components/ui/formStyles.ts` | **New file** — exports all form class constants |
| `frontend/src/pages/Connections.tsx` | Import and use shared constants |
| `frontend/src/pages/RunsPage.tsx` | Import and use shared constants |
| `frontend/src/components/PlanForm.tsx` | Replace local `INPUT_CLASS`/`LABEL_CLASS` |
| `frontend/src/components/StepEditorModal.tsx` | Replace local `INPUT_CLASS`/`LABEL_CLASS` |

### Constants to export

```ts
// formStyles.ts
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

// Alert/banner blocks (used for inline error/success/warning/info panels)
export const ALERT_BASE = 'rounded-md border px-4 py-3 text-sm'
export const ALERT_ERROR = `${ALERT_BASE} bg-error-bg border-error-border text-error-text`
export const ALERT_SUCCESS = `${ALERT_BASE} bg-success-bg border-success-border text-success-text`
export const ALERT_WARNING = `${ALERT_BASE} bg-warning-bg border-warning-border text-warning-text`
export const ALERT_INFO = `${ALERT_BASE} bg-info-bg border-info-border text-info-text`
```

### Implementation notes
- `PlanForm.tsx` and `StepEditorModal.tsx` likely already have local `INPUT_CLASS` / `LABEL_CLASS`
  constants — remove the local definitions and import from the shared file.
- `Connections.tsx` and `RunsPage.tsx` have inline form input strings; replace with shared
  constants.
- Do not change form layout or field arrangement — only class strings.
- Check browser autofill styling in both modes; if autofill text/background clashes with the token system, add a small targeted fix in the shared form styles layer rather than per-page overrides.
- If any file uses a `<select>` without a class constant, apply `SELECT_CLASS`.

### Acceptance criteria
- `formStyles.ts` exists and exports all constants listed above.
- No file in `src/` defines a local `INPUT_CLASS` or `LABEL_CLASS`.
- Inputs, selects, and textareas render correctly in both light and dark mode.
- `npm run typecheck` passes.

---

## Ticket 3 — Atomic UI Components — ✅ DONE

### Goal
Migrate `Button`, `Badge`, `Progress`, and `EmptyState` to use semantic tokens. Remove any
existing `dark:` variants from these files.

### Affected files
`frontend/src/components/ui/Button.tsx`
`frontend/src/components/ui/Badge.tsx`
`frontend/src/components/ui/Progress.tsx`
`frontend/src/components/ui/EmptyState.tsx`

### Implementation notes

**Button**
- `primary` variant: prefer semantic accent classes (`bg-accent hover:bg-accent-hover text-content-inverse`). If there is a short-term need to keep `bg-blue-600 hover:bg-blue-700`, treat that as a temporary bridge rather than the long-term target.
- `secondary` variant: replace `bg-white text-gray-700 border-gray-300 hover:bg-gray-50` with
  `bg-surface-raised text-content-primary border-border-strong hover:bg-surface-hover`.
- `ghost` variant: replace `text-gray-600 hover:bg-gray-100` with
  `text-content-secondary hover:bg-surface-hover`.
- `danger` variant: label/icon text should use `text-content-inverse`; button bg can stay red.
- For disabled button treatment, verify reduced opacity still leaves labels legible in dark mode; add a tokenised disabled surface/text treatment if opacity alone feels weak.
- Focus ring: `focus:ring-border-focus` instead of `focus:ring-blue-500`.

**Badge**
- Badge has many semantic variants (success, error, warning, info, pending, etc.). Map each to
  its closest state token:
  - success → `bg-success-bg text-success-text`
  - error/failed → `bg-error-bg text-error-text`
  - warning → `bg-warning-bg text-warning-text`
  - info → `bg-info-bg text-info-text`
  - neutral/pending/aborted → `bg-surface-sunken text-content-muted`
  - running/in_progress → use `bg-info-bg text-info-text` or a dedicated blue badge class
- Document the variant-to-token mapping within the component file as a comment for future
  maintainers.

**Progress**
- Track: `bg-gray-200` → `bg-surface-sunken`
- Label: `text-gray-500` → `text-content-muted`
- Value: `text-gray-700` → `text-content-secondary`
- Fill colours (blue, green, red, orange) retain Tailwind palette classes — they read
  acceptably in both modes.

**EmptyState**
- Title: `text-gray-900` → `text-content-primary`
- Description: `text-gray-500` → `text-content-muted`
- Icon: remove `dark:text-gray-600`; use `text-content-disabled` token instead.

### Acceptance criteria
- All four components use only token classes (no raw `text-gray-*`, `bg-white`, `bg-gray-*`,
  `border-gray-*` without a `dark:` companion).
- No `dark:` variants remain in these files — tokens are the single source of truth.
- All Badge variants are visually distinguishable in both modes.
- `npm run typecheck` and `npm run test:run` pass.

---

## Ticket 4 — Container UI Components — ✅ DONE

### Goal
Migrate `Card`, `Modal`, `Tabs`, and `Toast` to use semantic tokens. Apply a shared overlay shadow
policy to Modal and Toast.

### Affected files
`frontend/src/components/ui/Card.tsx`
`frontend/src/components/ui/Modal.tsx`
`frontend/src/components/ui/Tabs.tsx`
`frontend/src/components/ui/Toast.tsx`

### Implementation notes

Add a shared overlay shadow constant or utility (for example `OVERLAY_SHADOW_CLASS = 'shadow-xl shadow-black/10 dark:shadow-black/40'`) and reuse it anywhere an overlay panel needs depth.

**Card**
- Container: `bg-white border-gray-200` → `bg-surface-raised border-border-base`
- Header border: `border-gray-200` → `border-border-base`
- Title: `text-gray-900` → `text-content-primary`
- Subtitle: `text-gray-500` → `text-content-muted`

**Modal**
- Backdrop: `bg-black/40` — acceptable as-is in both modes.
- Panel: `bg-white` → `bg-surface-elevated`
- Header: `border-gray-200` → `border-border-base`
- Title: `text-gray-900` → `text-content-primary`
- Description: `text-gray-500` → `text-content-muted`
- Footer: `bg-gray-50 border-gray-200` → `bg-surface-sunken border-border-base`
- Shadow: The panel should carry a shadow in both modes. Apply
  `shadow-xl shadow-black/10 dark:shadow-black/40` — reduced opacity variant in dark mode.
  This is an exception to the no-shadow-for-elevation rule; modals are overlay elements.

**Tabs**
- Tab list border: `border-gray-200` → `border-border-base`
- Inactive tab text: `text-gray-500` → `text-content-muted`
- Inactive hover: `hover:text-gray-700` → `hover:text-content-secondary`,
  `hover:border-gray-300` → `hover:border-border-strong`
- Active tab text: `text-blue-600` is fine in both modes.
- Active tab border (underline): `border-blue-600` stays.
- Focus ring: `focus-visible:ring-blue-500` → `focus-visible:ring-border-focus`

**Toast**
- Container: `bg-white` → `bg-surface-elevated`
- Add `border border-border-base` if not already present (gives definition in dark mode).
- Message: `text-gray-800` → `text-content-primary`
- Close button: `text-gray-400 hover:text-gray-600` → `text-content-muted hover:text-content-secondary`
- Each variant's left-border accent colour (`border-l-4 border-red-500` etc.) can stay as-is —
  the coloured accent reads well in both modes.
- Shadow: Toasts are overlay elements; apply the shared overlay shadow policy rather than inventing a separate local value.

### Acceptance criteria
- All four components use only token classes (no unaccompanied `dark:`-less gray/white classes).
- Modal has a visible but restrained shadow in dark mode.
- Toasts display correctly in both modes with clear colour-coded variants.
- `npm run typecheck` and `npm run test:run` pass.

---

## Ticket 5 — DataTable — ✅ DONE

### Goal
Migrate `DataTable` to use semantic tokens. DataTable is used on every data page, so this
ticket directly improves all pages.

### Affected files
`frontend/src/components/ui/DataTable.tsx`

### Implementation notes
- Outer wrapper: add `bg-surface-raised` if any background is set; ensure it doesn't bleed
  white into dark backgrounds.
- `overflow-x-auto` wrapper: no colour, no change needed.
- `<thead>`: `bg-gray-50` → `bg-surface-sunken`
- Header `<th>` text: `text-gray-500` → `text-content-muted`
- `<tbody>` dividers: `divide-y divide-gray-200` → `divide-y divide-border-base`
- `<tbody>` hover: `hover:bg-gray-50` → `hover:bg-surface-hover`
- Body `<td>` text: `text-gray-900` → `text-content-primary`
- Empty-state message: `text-gray-400` → `text-content-muted`
- If DataTable supports a persistent selected row state anywhere in the app, use `bg-surface-selected text-content-selected` rather than reusing hover or generic info-alert styling.
- Column header borders: `border-gray-200` → `border-border-base`

### Acceptance criteria
- DataTable renders correctly in both modes with legible text and visible row separators.
- Hover state is visible in both modes.
- No `dark:`-less gray/white classes remain in the file.
- `npm run typecheck` passes.

---

## Ticket 6 — Dashboard Page — ✅ DONE

### Goal
Migrate `Dashboard.tsx` to semantic tokens. This is a self-contained page with no sub-components
to update.

### Affected files
`frontend/src/pages/Dashboard.tsx`

### Implementation notes
- Stat card values: `text-gray-900` → `text-content-primary`
- Stat card labels: `text-gray-500` → `text-content-muted`
- API status label: `text-gray-500` → `text-content-muted`
- Loading skeleton/placeholder: `text-gray-300` → `text-content-disabled`
- Inline table (if present — not using DataTable): apply the same token mapping as DataTable
  ticket. If it is using the `DataTable` component, no further changes needed there.
- Table header text: `text-gray-500` → `text-content-muted`
- Table row hover: `hover:bg-gray-50` → `hover:bg-surface-hover`
- Row dividers: `divide-gray-200` / `divide-gray-100` → `divide-border-base`
- Link text: `text-blue-600` → `text-content-link`
- If the page includes a current-selection treatment distinct from hover, use selected-state tokens rather than stronger hover colours.
- Cell text variants: `text-gray-600` / `text-gray-700` → `text-content-secondary`
- Status colours in cells (`text-green-700`, `text-red-700`): → `text-success-text` /
  `text-error-text` respectively.

### Acceptance criteria
- Dashboard is legible and well-contrasted in both light and dark mode.
- No unaccompanied `dark:`-less gray/white classes remain in this file.
- `npm run typecheck` passes.

---

## Ticket 7 — Connections Page — ✅ DONE

### Goal
Migrate `Connections.tsx` to semantic tokens. This is the most form-heavy page and the first
to use the shared `formStyles.ts` constants from Ticket 2 alongside the token system.

### Affected files
`frontend/src/pages/Connections.tsx`

### Implementation notes
- All `<input>`, `<select>`, `<textarea>` fields: replace with `INPUT_CLASS` / `SELECT_CLASS`
  from `formStyles.ts` (should already be done if Ticket 2 covered this file — verify and
  clean up any remaining instances).
- All `<label>` elements: replace with `LABEL_CLASS`.
- Error alert panels: replace with `ALERT_ERROR` from `formStyles.ts`.
- Test result panel (success): replace colours with `ALERT_SUCCESS`.
- Test result panel (failure): replace colours with `ALERT_ERROR`.
- Breadcrumb links: `text-gray-500 hover:text-gray-900` → `text-content-muted hover:text-content-primary`
- Table action button text (`text-gray-600`, `hover:text-gray-900`): →
  `text-content-secondary hover:text-content-primary`
- Section headings (`text-gray-900`): → `text-content-primary`
- Section descriptions (`text-gray-500`): → `text-content-muted`
- Any remaining inline grey backgrounds or borders: apply surface/border tokens.
- Existing `dark:` variants throughout the file: remove once token equivalents are in place.

### Acceptance criteria
- Full Connections page (both Salesforce and S3 sections) is legible in both modes.
- Create/edit modal, test result panels, and table all theme correctly.
- No unaccompanied `dark:`-less gray/white classes remain.
- `npm run typecheck` passes.

---

## Ticket 8 — Plans Pages — ✅ DONE

### Goal
Migrate `PlansPage.tsx`, `PlanEditor.tsx`, `PlanForm.tsx`, `StepList.tsx`,
`StepEditorModal.tsx`, and `PreflightPreviewModal.tsx` to semantic tokens.

### Affected files
`frontend/src/pages/PlansPage.tsx`
`frontend/src/pages/PlanEditor.tsx`
`frontend/src/components/PlanForm.tsx`
`frontend/src/components/StepList.tsx`
`frontend/src/components/StepEditorModal.tsx`
`frontend/src/components/PreflightPreviewModal.tsx`

### Implementation notes

**PlansPage**
- Table cells and status text: apply content tokens.
- Error alert: `ALERT_ERROR`.

**PlanEditor**
- Breadcrumb: `text-content-muted hover:text-content-primary` / current crumb `text-content-primary`.
- Step card containers (`bg-gray-50`, `border-gray-200`): → `bg-surface-sunken border-border-base`.
- Preview result panels (inline bg-blue-50, bg-red-50): → `bg-info-bg border-info-border` /
  `bg-error-bg border-error-border`.
- Form labels / inputs: use `formStyles.ts` constants.
- Preflight modal trigger and any inline grey panels: apply surface tokens.
- Any remaining `dark:` variants: migrate to tokens.

**PlanForm**
- Replace local `INPUT_CLASS` / `LABEL_CLASS` with imports from `formStyles.ts`.
- Error alert: `ALERT_ERROR`.

**StepList**
- Step row background and text: `bg-surface-raised text-content-primary`.
- Step sequence badge: `bg-info-bg text-info-text` or `bg-accent-soft text-content-selected` if the info treatment feels too semantically loud in context.
- Meta text (`text-gray-500`, `text-gray-400`): → `text-content-muted`.
- Preview container states:
  - Loading/idle: `bg-surface-sunken`
  - Success: `bg-success-bg border-success-border text-success-text`
  - Error: `bg-error-bg border-error-border text-error-text`
  - Warning (partial match): `bg-warning-bg border-warning-border text-warning-text`

**StepEditorModal**
- Replace local `INPUT_CLASS` / `LABEL_CLASS`.
- Error alert: `ALERT_ERROR`.
- Help text: `text-content-muted`.

**PreflightPreviewModal**
- Container border: `border-border-base`.
- Status text variants: map to state tokens.
- Loading spinner: no change needed.

### Acceptance criteria
- All Plans pages and components are legible in both modes.
- No unaccompanied `dark:`-less gray/white classes remain in any of these files.
- PlanEditor preview panels clearly indicate state (success/error/warning) in both modes.
- `npm run typecheck` and `npm run test:run` pass.

---

## Ticket 9 — Runs Pages — ✅ DONE

### Goal
Migrate `RunsPage.tsx`, `RunDetail.tsx`, and its sub-components (`RunSummaryCard`,
`RunStepPanel`, `RunJobList`) to semantic tokens.

### Affected files
`frontend/src/pages/RunsPage.tsx`
`frontend/src/pages/RunDetail.tsx`
`frontend/src/components/RunSummaryCard.tsx` (or equivalent path)
`frontend/src/components/RunStepPanel.tsx` (or equivalent path)
`frontend/src/components/RunJobList.tsx` (or equivalent path)

> Note: RunDetail sub-components may be co-located in `RunDetail.tsx` or in a `RunDetail/`
> subdirectory. Check the actual file layout before implementing.

### Implementation notes

**RunsPage**
- Filter label: `text-content-secondary`.
- Filter inputs/selects: `SELECT_CLASS` / `INPUT_CLASS` from `formStyles.ts`.
- Table and row styling: tokens applied consistently (should flow from DataTable if the
  component is used; check for any inline table overrides).

**RunDetail**
- Sticky header `bg-white` → `bg-surface-raised`; add `border-b border-border-base`.
- Step accordion header: `bg-gray-50 hover:bg-gray-100` → `bg-surface-sunken hover:bg-surface-hover`
- Stat values: `text-content-primary`
- Breadcrumb: `text-content-muted hover:text-content-primary`

**RunSummaryCard**
- Container: `bg-surface-raised border-border-base`
- Heading: `text-content-primary`
- Stat labels: `text-content-muted`
- Stat values: `text-content-primary`
- Success count: `text-success-text`
- Error count: `text-error-text`
- Meta text: `text-content-muted`
- Plan link: `text-content-link`
- Error summary box: `ALERT_ERROR` from `formStyles.ts`
- Live indicator dot: `text-blue-500` can stay (decorative).

**RunStepPanel**
- Container: `border-border-base`
- Header: `bg-surface-sunken hover:bg-surface-hover`
- Heading: `text-content-primary`
- Meta: `text-content-muted`
- Chevron: `text-content-muted`

**RunJobList**
- Row hover: `hover:bg-surface-hover`
- Part label: `text-content-muted`
- Record counts: `text-content-secondary`
- Error text: `text-error-text`
- Job link: `text-content-link`
- Empty message: `text-content-muted`

### Acceptance criteria
- RunsPage filter controls are legible in both modes.
- RunDetail mission-control view is fully themed — sticky header, step accordion, job rows.
- Status-specific colours (success/error counts) are clear in both modes.
- No unaccompanied `dark:`-less gray/white classes remain in any of these files.
- `npm run typecheck` and `npm run test:run` pass.

---

## Ticket 10 — JobDetail and FilesPage — ✅ DONE

### Goal
Migrate `JobDetail.tsx` and `FilesPage.tsx` to semantic tokens. Both pages have partial dark
mode support already; this ticket completes and migrates that to the token system.

### Affected files
`frontend/src/pages/JobDetail.tsx`
`frontend/src/pages/FilesPage.tsx`

### Implementation notes

**JobDetail**
- Breadcrumb: `text-content-muted hover:text-content-primary`; current crumb `text-content-primary`.
- Metadata labels (`text-gray-500`): → `text-content-muted`
- Metadata values (`text-gray-900`): → `text-content-primary`
- Error message panel: `ALERT_ERROR`
- JSON payload block (`bg-gray-900 text-gray-100`): This is intentionally inverted (a dark
  code-block aesthetic). Keep this styling as-is — it reads well in both modes. This establishes a general rule that code/preformatted blocks may use a deliberately fixed high-contrast scheme where that is clearer than tokenised surface styling.
- Download row containers: `bg-surface-sunken border-border-base`
- Download button: use existing `Button` component variants.

**FilesPage**
- File list dividers: `divide-border-base`
- Selected item: prefer `bg-surface-selected text-content-selected` (currently `bg-blue-50 text-blue-700`). Use info tokens only if the selected state is intended to read as informational, not merely selected.
- Unselected hover: `hover:bg-surface-hover text-content-primary`
- File metadata: `text-content-muted`
- Source label and select: use `LABEL_CLASS` / `SELECT_CLASS` from `formStyles.ts`.
- Error box: `ALERT_ERROR`
- Existing `dark:` variants (breadcrumb, source select, etc.): migrate to tokens and remove
  inline `dark:` classes.

### Acceptance criteria
- JobDetail metadata, download section, and breadcrumb are legible in both modes.
- FilesPage file list selected/hover states are clearly visible in both modes.
- No unaccompanied `dark:`-less gray/white classes remain in either file.
- Existing `dark:` variants are removed in favour of token classes.
- `npm run typecheck` passes.

---

## Completion Criteria

## Quality Gates and Verification

Before sign-off, verify the following in both light and dark mode:
- Body text, secondary text, muted text, and disabled text remain readable against their intended surfaces.
- Focus rings are visible on forms, tabs, and buttons on both light and dark backgrounds.
- Hover, active, and selected states are all visually distinguishable without relying solely on tiny border changes.
- State panels (error/success/warning/info) are clearly distinguishable and not overly saturated.
- Browser autofill does not introduce unreadable text/background combinations in inputs.
- Modal, toast, and dropdown/popup surfaces read as overlays via elevated surface + border + restrained shadow.

## Completion Criteria

All ten tickets done means:
- Every component and page uses only token classes or Tailwind palette classes that read well
  in both modes (e.g. blue accent colours).
- No ordinary colour `dark:` variants remain in any component or page file — the token system is the single
  source of truth. Rare documented exceptions may exist for shadow tuning, browser quirks, or third-party patching.
- `AppShell.tsx`, `Login.tsx`, `FilePicker.tsx`, `CsvPreviewPanel.tsx`, and `ComboInput.tsx`
  have had their existing `dark:` variants migrated to tokens as part of their respective
  tickets.
- `npm run build`, `npm run typecheck`, and `npm run test:run` all pass cleanly.
- Both light and dark mode have been visually verified across all routes.
- A final cleanup sweep has been completed for lingering `dark:` classes, raw `bg-white`/`text-gray-*`/`border-gray-*` theming classes, and duplicate local form-style constants.
