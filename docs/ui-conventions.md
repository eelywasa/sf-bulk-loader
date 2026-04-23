# UI Conventions

This document is the reference for anyone writing or modifying frontend code. It covers
the design token system, component usage, form styling, and the rules that keep the
interface consistent across light and dark mode.

> **Maintenance rule:** This document must be kept in sync with the code. Any change to
> the token system, `formStyles.ts`, shared components, or the conventions themselves
> must include a corresponding update to the relevant section here as part of the same
> task. Do not defer documentation updates to a later ticket.

**Design canvas:** the long-form design reference (artboards, component frames, both themes
side-by-side) lives in the Bulk Loader UI Kit at
<https://claude.ai/design/p/a65bd36c-39dc-49fa-bf9c-dde83235133b>. The canvas is a
review aid, not a source of truth — tokens, `formStyles.ts`, and this doc are canonical.

---

## Design principles

The system optimises for five things, in priority order. When two conflict, the earlier wins.

1. **Precision over personality.** Bulk Loader is a tool for Salesforce admins moving
   millions of records. The visual language is flat, dense, and legible — not playful.
2. **Token-driven theming.** Every colour that differs between light and dark is a semantic
   token. Components never contain `dark:` variants for ordinary theming. See below.
3. **Elevation through surface step, not shadow.** Dark mode in particular expresses depth
   by stepping from `surface-base` → `surface-raised` → `surface-elevated`. Shadows are
   reserved for true overlays.
4. **Semantic component names.** `DataTable`, `CsvPreviewPanel`, `Badge`, `EmptyState` — not
   `BlueBox`, `StripedRows`. Pick a component by meaning, not by look.
5. **A small vocabulary, used consistently.** One primary button per section. One empty-state
   pattern. One error-alert style. Novel visual treatments need an RFC, not a local
   `className`.

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

### Three layers, in order of precedence

1. **Primitives** — raw Tailwind palette (`gray.900`, `blue.500`). **Do not use directly in
   components.**
2. **Semantic tokens** — `surface-raised`, `content-primary`, `border-base`. **This is what
   component code reads.**
3. **Component constants** — `INPUT_CLASS`, `LABEL_CLASS`, `ALERT_ERROR` from
   `formStyles.ts`. **This is what pages read for form and feedback patterns.**

Writing `bg-gray-900` in a component breaks layer separation. Writing
`bg-[var(--color-surface-raised)]` is legal but wasteful — use the `bg-surface-raised`
Tailwind utility instead.

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
| `bg-surface-banded` † | slate-50 | `#0b1220` | Row banding in wide data tables (CSV preview) |
| `bg-surface-code` ‡ | gray-900 | gray-900 | Log output, code blocks, SOQL panels |
| `bg-surface-hover` | gray-50 | gray-800 | Row/item hover state |
| `bg-surface-active` | gray-100 | gray-700 | Pressed/activated state |
| `bg-surface-selected` | blue-50 | blue-950 | Persistently selected rows/items |
| `bg-scrim` ‡ | `rgb(0 0 0 / 0.4)` | `rgb(0 0 0 / 0.6)` | Modal/dialog backdrops |

† `surface-banded` sits between `base` and `raised` to give wide tables a subtle scanning
cue without competing with error/warning cell backgrounds. Adopted by `CsvPreviewPanel`
row banding (see SFBL-226 / SFBL-229 for consumer migrations).

‡ `surface-code` / `content-code` / `scrim` intentionally resolve to the **same value in
both themes** — code blocks read best on a dark background in any theme, and a scrim's job
is to dim the page beneath a modal. Scrim is opacity-on-black, not a token alias.

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
| `text-content-code` ‡ | gray-100 | gray-100 | Text inside `surface-code` log/code blocks |

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

### Danger tokens

Distinct from `error-*` (which is for validation messaging). Use `danger` for destructive
actions — delete, abort, permanent removal — and always pair with a confirmation step.

| Utility class | Light | Dark | Use for |
|---|---|---|---|
| `bg-danger` / `text-danger` | red-600 | red-500 | Destructive-action buttons and icons |
| `bg-danger-hover` | red-700 | red-400 | Hover state for destructive elements |

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

### Log, code, and SOQL blocks

Use `bg-surface-code` + `text-content-code` on `<pre>` / `<code>` elements that
render raw payloads — SOQL snippets, Salesforce API response JSON, run logs.
These tokens intentionally resolve to the same dark values in light and dark
mode, so code blocks have consistent contrast on any page surface.

```tsx
// ✅ theme-consistent log/code block
<pre className="rounded-md bg-surface-code text-content-code px-3 py-2 text-xs font-mono whitespace-pre-wrap">
  {soql}
</pre>
```

Do not use `bg-gray-900 text-gray-100` on log/code surfaces — it bypasses the
token layer and disappears from future palette tuning (see HANDOVER §4.3).

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

### Legitimate `dark:` exceptions

Tokens resolve dark-mode values automatically, so `dark:` variants are reserved for cases
tokens cannot express. Across the codebase the total should stay under ten. Legitimate
cases:

- Overlay shadow tuning — already captured in `OVERLAY_SHADOW_CLASS`.
- Browser autofill colour override.
- Third-party component patches.

Document each exception with an inline comment. Anything outside those three is a missing
token — escalate per "Adding to the system".

---

## Dark-mode review checklist

Walk this list in **both** themes before merging any new component or major visual change.

- [ ] All colours come from tokens. No raw hex, no `dark:` classes (except the documented
  exceptions above).
- [ ] Elevation works without shadow (surface step + border). Overlays use
  `OVERLAY_SHADOW_CLASS`.
- [ ] Focus ring visible on every surface the component can sit on (`surface-base`,
  `surface-raised`, `surface-elevated`).
- [ ] Selected state uses `surface-selected` + `content-selected`. Contrast ≥ 3:1 vs.
  adjacent rows in both themes.
- [ ] Cell-level state colours (`bg-error-bg`, `bg-warning-bg`) remain visually dominant
  against any row banding or hover state.
- [ ] Icons use `text-content-muted` or `text-content-primary`, never a raw colour class.
- [ ] Skeleton loaders use `surface-sunken`.
- [ ] Brand marks, avatars, inline images legible on both `surface-base` and
  `surface-raised`.

---

## Typography

### Families

```css
--font-sans: "IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI",
             Roboto, "Helvetica Neue", Arial, sans-serif, "Apple Color Emoji",
             "Segoe UI Emoji";
--font-mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Monaco,
             Consolas, "Liberation Mono", "Courier New", monospace;
```

IBM Plex Sans and Mono are pair-designed — cap-height and x-height align in mixed strings
like `Plan name · abc123…`, which matters in run detail and CSV preview. Plex Mono has a
dotted zero (not slashed), important when `0` appears next to hex UUIDs. The system stack
is the fallback so first paint is never blank.

**Self-host:** fonts load from `@fontsource/ibm-plex-sans` and `@fontsource/ibm-plex-mono`
(imported from `src/main.tsx`) — not Google Fonts. License: SIL OFL.

**Weights used:** Sans 400 / 500 / 600 / 700. Mono 400 / 500.

### Scale

Tailwind default scale (rem-based). Do not introduce new sizes without an RFC.

| Token | px | Use for |
|---|---|---|
| `text-xs` | 12 | Badges, helper text, metadata |
| `text-sm` | 14 | Body, inputs, table cells |
| `text-base` | 16 | Default body, card titles |
| `text-lg` | 18 | Section headings within a page |
| `text-xl` | 20 | Page section headers |
| `text-2xl` | 24 | Page titles |
| `text-3xl` | 30 | Hero/marketing only (rare) |

**Floor:** never below `text-xs` (12 px). CSV preview cells use `text-xs` mono; that's the
minimum.

### Weight usage

- **400** — body, default table cells
- **500** — labels, badge text, nav items
- **600** — headings, card titles, button labels
- **700** — page titles only

Italic is used only for empty-cell markers (`(empty)` in CSV preview) — never for emphasis.

### Mono surfaces

Use `font-mono` on:

- CSV preview cells (all, including headers)
- Run IDs, trace IDs, timestamps shown as identifiers (not as prose)
- SOQL inputs and output blocks
- File names, paths, column names referred to inline (`<code>`)

Don't use mono for UI labels or prose — it's data, not chrome.

---

## Spacing, radii, shadows

### Spacing

Tailwind default 4 px scale (`p-1` = 4 px, `p-2` = 8 px, …). Rhythm inside components:

- **Dense tables** — `px-3 py-1` cells, `px-4 py-2` headers
- **Cards** — `p-4` default, `p-6` for feature cards
- **Page padding** — `p-6` default
- **Form field gap** — `gap-1.5` between label and input, `gap-4` between fields

### Radii

```css
--radius-sm:   4px;    /* badges, small chips */
--radius-md:   6px;    /* inputs, buttons, cards */
--radius-lg:   8px;    /* modals, large panels */
--radius-full: 9999px; /* avatars, progress tracks, pill badges */
```

Tailwind exposes these as `rounded-sm`, `rounded-md`, `rounded-lg`, `rounded-full`. Do not
use `rounded-xl` / `rounded-2xl` — reserved for brand/marketing, which doesn't exist yet.

### Shadows

```css
--shadow-sm:      0 1px 2px 0 rgb(0 0 0 / 0.05);          /* rare; pinned toolbars */
--shadow-overlay: 0 20px 25px -5px rgb(0 0 0 / 0.10),     /* modals, dropdowns, toasts */
                  0 8px 10px -6px rgb(0 0 0 / 0.10);
```

Access the overlay shadow via `OVERLAY_SHADOW_CLASS` from `formStyles.ts` so dark-mode
tuning stays consistent. Cards,
panels, and table rows use **no shadow** — rely on `border-border-base` sitting on the
parent surface step.

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

| Component | When to use | Notes |
|---|---|---|
| `Card` | Any bordered panel grouping related content | `border border-border-base`, no shadow |
| `Button` | All interactive buttons | Variants: `primary`, `secondary`, `ghost`, `danger`. One primary per section |
| `Badge` | Status labels, counts, tags | Variants map 1:1 to state tokens |
| `DataTable` | Server-curated rows (runs, plans, users) | Small N, known columns. **Not for CSV previews.** See "DataTable vs CsvPreviewPanel" below |
| `Modal` | Dialogs requiring user action | Uses `surface-elevated` + `OVERLAY_SHADOW_CLASS` |
| `Tabs` | Switching between content panels within a page | Active tab uses `border-border-focus` underline |
| `Toast` | Transient feedback | Auto-dismiss 5 s default; error toasts manual dismiss |
| `EmptyState` | Zero-item states in lists and tables | No duplicate CTA if page header already has one |
| `Progress` | Percentage or step-based progress | Use `Progress`, not raw `<progress>`. `color` = `blue \| green \| red \| orange`; all driven by tokens (`bg-accent` / `bg-success-text` / `bg-danger` / `bg-warning-text`) |
| `Spinner` | Indeterminate loading indicator | `size` = `xs \| sm \| md \| lg`; `border-accent`; honours `prefers-reduced-motion` |
| `BrandMark` | App hexagon logo next to "Bulk Loader" wordmark | `size` = `sm \| md \| lg`; `bg-brand`; `aria-hidden` — always pair with a visible wordmark |
| `RequiredAsterisk` | Required-field marker inside a `<label>` | `text-error-text` + visually hidden " (required)" for screen readers. Always pair with native `required` / `aria-required` on the input |
| `CsvPreviewPanel` | All CSV file preview contexts | Virtualized, mono cells, cell-level state overlays |
| `ComboInput` | Text input with autocomplete suggestions | — |
| `PermissionGate` | Conditionally render UI based on RBAC | Never for route protection — that's `ProtectedRoute` |

### Before building new

Ask, in order:

1. Does an existing component do this? Use it. Extend props before forking.
2. Is this a 1–2 screen pattern? Inline it with tokens; don't create a component.
3. Is this a ≥3 screen pattern? Propose in an RFC; add to `src/components/ui/`.

**Never** create a new shared component in a `pages/` folder. Shared = `components/ui/`.
Page-specific composition = `pages/*/components/`.

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

### Button styling for non-`<button>` elements

Some CTAs must render as `<a>` or React Router `<Link>` (e.g. navigation back
to the dashboard, or links in empty states). Wrapping a `<Link>` in
`<Button>` yields invalid nested-interactive markup, so instead apply the
shared class constants from `formStyles.ts`:

```tsx
import { BUTTON_PRIMARY_CLASS } from '../components/ui/formStyles'

<Link to="/" className={BUTTON_PRIMARY_CLASS}>Back to dashboard</Link>
```

Available: `BUTTON_PRIMARY_CLASS`, `BUTTON_SECONDARY_CLASS`,
`BUTTON_GHOST_CLASS`, `BUTTON_DANGER_CLASS`. Each bakes in the `md` size. For
dynamic sizes, use the `<Button>` component — `BUTTON_BASE_CLASS` and the
`BUTTON_*_COLORS` variant strings are the composition primitives the component
itself reads, and are exported for the same reason. Prefer `<button>` behind a
confirmation step for destructive actions; `BUTTON_DANGER_CLASS` exists for
symmetry and the rare `<Link>`-shaped destructive CTA.

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

## DataTable vs CsvPreviewPanel

These solve different problems. Mixing them is the most common mistake in PRs.

| Concern | `DataTable` | `CsvPreviewPanel` |
|---|---|---|
| **Data source** | Server-curated rows | Raw CSV parse |
| **Row count** | 10–100 | Up to millions — virtualized |
| **Column count** | 4–8, fixed schema | 10–200, unknown headers |
| **Column widths** | Flex to fill | Fixed (`min-w-[160px]`), horizontal scroll |
| **Cell content** | Formatted — badges, links, icons | Raw strings, mono |
| **Row click** | → detail page | Meaningless (no "detail" of a CSV row) |
| **Column click** | Sort | Map to Salesforce field |
| **Empty cells** | Usually impossible | Common — render `—` or `(empty)` |
| **Error display** | Alert above table | Cell-level background (state tokens) |
| **Font** | Sans | Mono |
| **Sticky header** | Yes | Yes (+ sticky row-number column) |
| **Row banding** | No | Yes — `surface-banded` on odd rows |

If a new feature wants "a table that previews data" — it's a `CsvPreviewPanel`, not a
`DataTable`. If it wants "a list of structured records with actions" — `DataTable`.

---

## Icon system

**Font Awesome (solid)** via `@fortawesome/react-fontawesome` and
`@fortawesome/free-solid-svg-icons`.

### Usage

```tsx
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faGaugeHigh, faPlay } from '@fortawesome/free-solid-svg-icons'

<FontAwesomeIcon icon={faGaugeHigh} className="w-4 h-4 flex-shrink-0" />
```

### Rules

- Colour via `text-content-*` tokens, never raw colour classes. Exception: state icons in
  `Toast` use `text-red-500` / `text-green-500` etc. as stated brand-accent dots, not theme
  colours.
- Size via explicit `w-/h-` utilities: `w-3 h-3` (12 px inline),
  `w-3.5 h-3.5` (14 px UI controls), `w-4 h-4` (16 px nav/buttons),
  `w-5 h-5` (20 px toasts), `w-12 h-12` (48 px empty states).
- Always include `flex-shrink-0` when an icon sits next to flex-grow text.
- Pick semantically: `faPlay` for run, `faFolderOpen` for files, `faListCheck` for plans —
  not by look.
- Decorative icons next to a text label should be `aria-hidden="true"`. Icons that carry
  meaning on their own need an `aria-label`.

### Adding a new icon

Import from `@fortawesome/free-solid-svg-icons` directly — no barrel/registry needed.
Tree-shaking handles unused.

### Don't mix icon libraries

The repo uses Font Awesome only. Do not introduce Lucide, Heroicons, Tabler, or one-off
inline `<svg>`. If the free-solid set is missing something, first choice is
`@fortawesome/free-regular-svg-icons` (not `pro`). Raise an RFC if pro is truly needed.

---

## State and feedback patterns

### Empty states

Use `EmptyState` for zero-item list/table states. Do not add a duplicate CTA button
inside `EmptyState` if there is already a primary action button in the page header — the
header button is always visible and is sufficient.

### Loading states

Show a loading indicator within the content area rather than replacing the whole page.
Prefer skeleton placeholders or a `<Spinner />` (from `components/ui`) inside the
relevant section. Do **not** roll a hand-crafted `border-blue-… animate-spin` span —
`<Spinner>` is the one canonical spinner: `border-accent`, `motion-safe:animate-spin`,
`role="status"` with a visually hidden "Loading…" label.

### Error states

Use `ALERT_ERROR` from `formStyles.ts` for inline error panels. Do not invent new
red-background patterns. For API errors surfaced in a table or list context, an
`EmptyState` with an error description is acceptable.

### Validation errors

Use `FIELD_ERROR_OUTLINE` on the field and `ERROR_TEXT_CLASS` on the message element
below it. Keep validation messages short and specific.

---

## Accessibility baseline

Target: **WCAG 2.2 AA**. Not negotiable — new components that fail this get rejected.

### Contrast

- **Body text on default surfaces:** ≥ 4.5:1.
- **Large text (`text-lg` bold or `text-xl`+):** ≥ 3:1.
- **UI chrome (borders, icons in context):** ≥ 3:1.
- **`content-primary` on `surface-raised`** and **`surface-banded`** — both pass with
  margin in light (≥ 14:1) and dark (≥ 12:1). Verified.
- **State `-text` tokens on their `-bg` tokens** — all pass. If adding a new state, verify.

### Focus

Every interactive element must show a visible focus ring. `border-border-focus`
(blue-500 / blue-400) is the default. Never remove focus styles — use `focus-visible:` if
reducing focus on mouse users is needed.

### Keyboard

- All `<button>`, `<a>`, and form controls reachable by Tab.
- Modals trap focus until closed; Esc closes.
- Actionable `DataTable` rows activate on Enter/Space via their native `<button>` or
  `<a>` wrapper.
- **Planned (SFBL-233):** skip link on `AppShell.tsx` so keyboard users can jump past the
  sidebar.
- **Planned:** arrow-key row navigation in `DataTable` / `CsvPreviewPanel` — not yet
  implemented; open an issue before assuming it.

### Screen readers

- Icons that carry meaning get `aria-label`. Icons next to a text label get
  `aria-hidden="true"`.
- Loading states announce via `aria-live="polite"`.
- **`role="alert"` is an assertive live region** — use it only for critical, time-sensitive
  messages that must interrupt the user (e.g. destructive-action confirmations, fatal
  errors). For non-critical feedback prefer `aria-live="polite"` on the container, or
  `role="status"` for brief status updates.

### Motion

Respect `prefers-reduced-motion`. A global CSS rule collapses transitions and animations
to effectively zero for users who opt in; new animated components must honour this too.

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

## In-app Help shell

The `/help` route (`src/pages/HelpPage.tsx`) is a two-pane documentation browser built at Vite build time from `docs/usage/*.md`. Key conventions:

### Content pipeline

`frontend/plugins/helpContent.ts` is a first-party Vite plugin. It reads all `docs/usage/*.md` files, parses YAML frontmatter with `gray-matter`, converts markdown to HTML via `unified` / `remark` / `rehype-slug` / `rehype-stringify`, and emits a virtual module:

```ts
import helpContent from 'virtual:help-content'
// helpContent.topics: HelpTopic[]  (sorted by nav_order)
```

Types live in `src/types/help.ts`. The ambient module declaration is in `src/types/virtual-modules.d.ts`. The plugin is referenced from `vite.config.ts` and its directory is included in `tsconfig.node.json`.

Internal cross-links within `docs/usage/` (`./other-topic.md#heading`) are rewritten to the app URL scheme (`/help#other-topic:heading`) by `src/utils/rewriteHelpLinks.ts` during the build pass.

### Deep-link URL scheme

`/help#<topic-slug>:<heading-id>`

- `topic-slug` — the `slug` frontmatter field of the target topic (stable; never renamed)
- `heading-id` — the `id` attribute on the rendered heading, generated by `rehype-slug` (GitHub-style: lowercase, spaces → hyphens)
- Omit `:<heading-id>` to link to the topic landing without scrolling to a specific heading

When building a link to a specific help topic from elsewhere in the app, use React Router's `<Link to="/help#topic-slug">` or `<Link to="/help#topic-slug:heading">`.

### Permission gating

Admin-only topics declare `required_permission` in frontmatter. The Help shell enforces two layers:

1. **Nav visibility** — `usePermission(required_permission)` hides the topic from the left-nav for users who lack the permission.
2. **Direct-access redirect** — if a user navigates directly to `/help#admin-topic` (e.g. via a bookmark), a `useEffect` detects the missing permission and calls `navigate('/403', { replace: true })`. This redirect is guarded by `isBootstrapping` to avoid false `/403`s during the auth bootstrap window.

This is the only place in the app where both layers are active simultaneously — the general rule ("use `ProtectedRoute` for URLs, use `PermissionGate` for in-page visibility") still applies, but `/help` is a single route whose "sub-pages" are hash-based, so both mechanisms are needed.

### Drift check

Any edit to `docs/usage/*.md` must be verified locally with:
```bash
node frontend/scripts/check-help-links.mjs
```
The `docs-drift` CI job enforces this on every PR.

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

---

## Proposing changes

1. **Small** (a token, a prop, a doc clarification) — direct PR with before/after reasoning.
2. **Medium** (new component, token group, convention) — RFC issue first: rationale,
   alternatives considered, impact on existing components. Discussion, then PR.
3. **Large** (font family, shadow scale overhaul, theming strategy) — RFC issue plus a
   design review in the UI Kit with at least two alternatives as artboards. No
   implementation PR until the design decision is approved.

**When in doubt, smaller.** Two focused PRs land faster than one sprawling PR.

---

## Appendix — known theming gaps

Theming questions without system-wide answers yet. Documented so each new component doesn't
invent its own rule.

| Gap | Notes |
|---|---|
| Image / chart dimming in dark mode | No system-wide rule. Most surfaces avoid it. Propose `filter: brightness(.85)` and open an issue before shipping. |
| Syntax-highlight palette in dark | CSV preview and SOQL inputs use `content-*` + state tokens only. `surface-code` / `content-code` cover log/code blocks (always-dark in both themes); richer syntax highlighting is a future RFC. |
| Opt-out-of-dark surfaces | Login, brand moments. Not used today. If needed, scope with an explicit `[data-theme="light"]` / `html.light` container, not negated utilities. |
| Forced-colors mode | Untested. Add `CanvasText` / `Canvas` fallbacks if a customer requests. |
| Chart and data-viz palette | Not yet defined. Any new chart must propose a palette as part of its RFC. |
