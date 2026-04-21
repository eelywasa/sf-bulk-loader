# UI Conventions

This document is the reference for anyone writing or modifying frontend code. It covers
the design token system, component usage, form styling, and the rules that keep the
interface consistent across light and dark mode.

> **Maintenance rule:** This document must be kept in sync with the code. Any change to
> the token system, `formStyles.ts`, shared components, or the conventions themselves
> must include a corresponding update to the relevant section here as part of the same
> task. Do not defer documentation updates to a later ticket.

---

## Theming architecture

The app uses a CSS custom property (design token) system defined in `src/index.css`.
Tailwind is configured to expose these tokens as utility classes, so components never
contain raw `dark:` variants for ordinary theming — the token resolves the correct value
for each mode automatically.

```
:root          →  light values
html.dark      →  dark values
tailwind.config.ts  →  maps CSS vars → Tailwind utilities (bg-surface-raised etc.)
```

Tailwind's `darkMode: 'class'` strategy is used. `ThemeContext.tsx` adds or removes the
`dark` class from `<html>` and persists the choice to `localStorage`.

---

## Token quick reference

### Surface tokens — backgrounds

| Utility class | Light | Dark | Use for |
|---|---|---|---|
| `bg-surface-base` | gray-50 | gray-950 | Page background |
| `bg-surface-raised` | white | gray-900 | Cards, panels, table bodies |
| `bg-surface-elevated` | white | gray-800 | Modals, dropdown panels |
| `bg-surface-overlay` | white | gray-800 | Toasts, popovers |
| `bg-surface-sunken` | gray-100 | gray-900 | Input fields, `<thead>`, code blocks |
| `bg-surface-hover` | gray-50 | gray-800 | Row/item hover state |
| `bg-surface-active` | gray-100 | gray-700 | Pressed/activated state |
| `bg-surface-selected` | blue-50 | blue-950 | Persistently selected rows/items |

### Content tokens — text and icons

| Utility class | Light | Dark | Use for |
|---|---|---|---|
| `text-content-primary` | gray-900 | gray-100 | Headings, primary values |
| `text-content-secondary` | gray-600 | gray-300 | Labels, secondary text |
| `text-content-muted` | gray-500 | gray-400 | Captions, metadata, placeholders |
| `text-content-placeholder` | gray-500 | gray-400 | Input placeholder text |
| `text-content-disabled` | gray-400 | gray-600 | Disabled controls and text |
| `text-content-inverse` | white | gray-900 | Text on solid-colour fills (e.g. primary button) |
| `text-content-link` | blue-600 | blue-400 | Anchor and navigation links |
| `text-content-selected` | blue-700 | blue-300 | Text/icons inside selected items |

### Border tokens

| Utility class | Light | Dark | Use for |
|---|---|---|---|
| `border-border-subtle` | gray-100 | gray-800 | Very soft row separators |
| `border-border-base` | gray-200 | gray-700 | Card borders, dividers, table lines |
| `border-border-strong` | gray-300 | gray-600 | Input/field borders |
| `border-border-focus` | blue-500 | blue-400 | Focus rings |

Also valid as divide and ring utilities: `divide-border-base`, `ring-border-focus`.

### Accent tokens

| Utility class | Light | Dark | Use for |
|---|---|---|---|
| `bg-accent` / `text-accent` | blue-600 | blue-400 | Primary action and accent colour |
| `bg-accent-hover` | blue-700 | blue-300 | Hover state for accent elements |
| `bg-accent-soft` | blue-50 | blue-950 | Soft accent backgrounds |

### State tokens — error / success / warning / info

Each state has three variants: `bg`, `border`, `text`.

| State | Example classes |
|---|---|
| Error | `bg-error-bg border-error-border text-error-text` |
| Success | `bg-success-bg border-success-border text-success-text` |
| Warning | `bg-warning-bg border-warning-border text-warning-text` |
| Info | `bg-info-bg border-info-border text-info-text` |

---

## Using tokens in practice

### Prefer tokens; avoid raw gray and white

```tsx
// ❌ breaks in dark mode
<div className="bg-white text-gray-900 border border-gray-200">

// ✅ tokens resolve correctly in both modes
<div className="bg-surface-raised text-content-primary border border-border-base">
```

### Avoid inline dark: variants for ordinary theming

`dark:` variants are reserved for edge cases that tokens cannot express — overlay shadow
tuning, browser autofill quirks, or third-party component patches. Document any such
exception with an inline comment.

```tsx
// ❌ this is what the token system replaces
<p className="text-gray-500 dark:text-gray-400">

// ✅
<p className="text-content-muted">

// ✅ acceptable exception (shadow tuning on overlay)
<div className="shadow-xl shadow-black/10 dark:shadow-black/40">
```

### Semantic state colours in table cells

```tsx
// ✅
<span className="text-success-text">{successCount}</span>
<span className="text-error-text">{errorCount}</span>
<span className="text-warning-text">{warningCount}</span>
```

### Selected vs hover states

Use distinct tokens so these states are visually separable:

```tsx
// row that can be hovered
<tr className="hover:bg-surface-hover cursor-pointer">

// row that is persistently selected
<tr className="bg-surface-selected text-content-selected">
```

---

## Elevation and shadows

Dark mode elevation is expressed through **surface colour steps and border contrast**,
not shadows. A card sitting on `bg-surface-base` uses `bg-surface-raised` — the step
difference creates the perception of lift.

**Shadows are reserved for overlay elements only** (modals, dropdowns, toasts):

```tsx
// Shared overlay shadow — apply to any floating surface
const OVERLAY_SHADOW = 'shadow-xl shadow-black/10 dark:shadow-black/40'
```

Do not add `shadow-md` or `shadow-lg` to cards, panels, or table rows.

---

## Form elements

All form element styling is centralised in `src/components/ui/formStyles.ts`. Import
constants from there rather than writing inline class strings.

```tsx
import {
  LABEL_CLASS,
  INPUT_CLASS,
  SELECT_CLASS,
  TEXTAREA_CLASS,
  CHECKBOX_CLASS,
  HELPER_TEXT_CLASS,
  ERROR_TEXT_CLASS,
  FIELD_ERROR_OUTLINE,
  FIELD_CONTAINER_CLASS,
  ALERT_ERROR,
  ALERT_SUCCESS,
  ALERT_WARNING,
  ALERT_INFO,
} from '../components/ui/formStyles'
```

### Field pattern

```tsx
<div className={FIELD_CONTAINER_CLASS}>
  <label htmlFor="name" className={LABEL_CLASS}>Connection name</label>
  <input id="name" className={INPUT_CLASS} />
  <p className={HELPER_TEXT_CLASS}>Used to identify this connection in plans.</p>
</div>
```

### Monospace textarea (e.g. SOQL, code inputs)

Use `TEXTAREA_CLASS` with an additional `font-mono` and size class. The `TEXTAREA_CLASS`
constant already includes `resize-y`:

```tsx
<textarea
  id="step-soql"
  rows={5}
  className={clsx(TEXTAREA_CLASS, 'font-mono text-xs')}
/>
```

### Inline validation results (non-field-level)

For actions that trigger server-side validation (e.g. Validate SOQL), render the result
inline below the action button using `ALERT_SUCCESS` or `ALERT_ERROR` from `formStyles.ts`.
Do not use custom colour classes:

```tsx
{validationResult === 'valid' && (
  <div className={`${ALERT_SUCCESS} mt-2`}>
    <p className="font-medium">Valid</p>
    <p className="text-xs mt-0.5">{summary}</p>
  </div>
)}
{validationResult === 'invalid' && (
  <div className={`${ALERT_ERROR} mt-2`}>
    <p className="font-medium">Validation failed</p>
    <p className="text-xs mt-0.5 font-mono whitespace-pre-wrap">{errorMessage}</p>
  </div>
)}
```

### Field with validation error

```tsx
<div className={FIELD_CONTAINER_CLASS}>
  <label htmlFor="url" className={LABEL_CLASS}>Instance URL</label>
  <input id="url" className={`${INPUT_CLASS} ${hasError ? FIELD_ERROR_OUTLINE : ''}`} />
  {hasError && <p className={ERROR_TEXT_CLASS}>{errorMessage}</p>}
</div>
```

### Alert banners

```tsx
// Inline error panel
<div className={ALERT_ERROR}>Failed to save connection.</div>

// Inline success panel
<div className={ALERT_SUCCESS}>Connection test passed.</div>
```

### Checkbox

```tsx
<label className="flex items-center gap-2 cursor-pointer select-none">
  <input type="checkbox" checked={checked} onChange={…} className={CHECKBOX_CLASS} />
  <span className="text-sm text-content-primary">{label}</span>
</label>
```

`CHECKBOX_CLASS` uses `border-border-strong` for the border and `text-accent` for the
checked-state fill colour, so it adapts to both themes without `dark:` variants.

### Rules

- Do not define local `INPUT_CLASS`, `LABEL_CLASS`, or equivalent constants in page or
  component files. Import from `formStyles.ts`.
- If a new form element type is needed (e.g. a checkbox group, a radio set), add its
  constant to `formStyles.ts` rather than writing it inline in one file.

---

## Shared UI components

Components live in `src/components/ui/`. Use them consistently rather than building
one-off equivalents inline.

| Component | When to use |
|---|---|
| `Card` | Any bordered panel grouping related content |
| `Button` | All interactive buttons; use the appropriate variant |
| `Badge` | Status labels, tags, and counts |
| `DataTable` | Any tabular data with columns and rows |
| `Modal` | Dialogs requiring user action before continuing |
| `Tabs` | Switching between content panels within a page |
| `Toast` | Transient feedback messages |
| `EmptyState` | Zero-item states in lists and tables |
| `Progress` | Percentage or step-based progress indicators |
| `CsvPreviewPanel` | All CSV file preview contexts |
| `ComboInput` | Text input with autocomplete suggestions |
| `PermissionGate` | Conditionally render UI based on RBAC permissions |

### PermissionGate

`src/components/PermissionGate.tsx` — conditionally renders children based on the current user's RBAC permissions.

```tsx
// Single permission check
<PermissionGate permission="connections.manage">
  <Button>New Connection</Button>
</PermissionGate>

// Any-of (OR) check
<PermissionGate any={['plans.manage', 'runs.execute']}>
  <ActionMenu />
</PermissionGate>

// All-of (AND) check
<PermissionGate all={['plans.manage', 'runs.execute']}>
  <AdvancedButton />
</PermissionGate>

// With fallback content
<PermissionGate permission="files.view_contents" fallback={<p>Access restricted.</p>}>
  <CsvPreviewPanel ... />
</PermissionGate>
```

Props:
- `permission?: string` — a single permission key; the gate passes if the user has it
- `any?: string[]` — an OR list; passes if the user has at least one
- `all?: string[]` — an AND list; passes if the user has all of them
- `fallback?: React.ReactNode` — rendered when the gate does not pass (default: `null`)

For imperative checks outside of JSX, use `usePermission(key)` (returns `boolean`) or `usePermissions()` (returns `Set<string>`) from `src/hooks/usePermission.ts`. Both hooks use `useAuthOptional()` internally and return `false` / empty `Set` when called outside an `AuthProvider`.

**Rule:** Never use permission checks to hide navigation items from the URL bar (that is route-level enforcement via `ProtectedRoute`). Use `PermissionGate` and `usePermission` for in-page element visibility only.

### Button variants

| Variant | Use for |
|---|---|
| `primary` (default) | Primary CTA — one per section |
| `secondary` | Secondary or neutral actions |
| `ghost` | Low-emphasis actions, icon-adjacent labels |
| `danger` | Destructive actions (delete, abort) — always pair with a confirmation step |

### Badge variants

Badges map to the state token system. Use the semantically correct variant rather than
choosing one because of its colour:

| Variant | Meaning |
|---|---|
| `success` | Completed, passed, active |
| `error` / `failed` | Failed, errored |
| `warning` | Partial success, needs attention |
| `info` | Informational, in-progress steps |
| `pending` / `neutral` | Not yet started, neutral state |
| `aborted` | Manually stopped |

---

## State and feedback patterns

### Empty states

Use `EmptyState` for zero-item list/table states. Do not add a duplicate CTA button
inside `EmptyState` if there is already a primary action button in the page header — the
header button is always visible and is sufficient.

### Loading states

Show a loading indicator within the content area rather than replacing the whole page.
Prefer skeleton placeholders or a spinner inside the relevant section.

### Error states

Use `ALERT_ERROR` from `formStyles.ts` for inline error panels. Do not invent new
red-background patterns. For API errors surfaced in a table or list context, an
`EmptyState` with an error description is acceptable.

### Validation errors

Use `FIELD_ERROR_OUTLINE` on the field and `ERROR_TEXT_CLASS` on the message element
below it. Keep validation messages short and specific.

---

## Adding to the system

### New token

If you find yourself writing `dark:` on a colour that appears in multiple places, you
need a new token rather than spreading raw classes:

1. Add the CSS custom property to `:root` and `html.dark {}` in `src/index.css`.
2. Add the corresponding Tailwind key in `tailwind.config.ts`.
3. Update this document's token table.
4. Replace all instances of the raw colour pair with the new token class.

### New form element constant

Add it to `src/components/ui/formStyles.ts` with a comment describing its intended use.

### New shared component

Add it to `src/components/ui/` and export it from the barrel index if one exists. Document
its purpose and props in a JSDoc comment on the component function. List it in the component
table above.

---

## Anti-patterns

| Pattern | Why to avoid | What to do instead |
|---|---|---|
| `bg-white` without a dark companion | Invisible in dark mode | `bg-surface-raised` |
| `text-gray-900` without a dark companion | Hard to read on dark surfaces | `text-content-primary` |
| `border-gray-200` without a dark companion | Disappears in dark mode | `border-border-base` |
| Local `INPUT_CLASS = '...'` constants | Drift from shared styles | Import from `formStyles.ts` |
| `dark:` on every element | Maintenance burden; will drift | Use tokens |
| `shadow-md` on cards or panels | Heavy in dark mode; elevation via surface steps only | Remove shadow; rely on `border-border-base` |
| Custom red/green alert panels | Duplicates state token system | Use `ALERT_ERROR` / `ALERT_SUCCESS` |
| Hardcoded hex or RGB colours | Bypasses both token and Tailwind systems | Add a token or use the nearest Tailwind palette class |
