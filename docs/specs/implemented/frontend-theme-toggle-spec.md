# Frontend Theme Toggle Spec

**Jira Epic: SFBL-13**

## Summary

Replace the "SF Bulk Loader v0.1" placeholder text at the bottom-left of the sidebar with a
**Settings** menu. The Settings menu exposes a **Theme** submenu with three options:
**Light**, **Dark**, and **System**.

---

## UI Design

### Entry point

The sidebar footer area (bottom of the left sidebar) contains a **Settings** button
instead of the version string. Clicking it opens a popover menu anchored above the button.

```
┌───────────────────┐
│  Dashboard        │
│  Connections      │
│  Load Plans       │
│  Runs             │
│  Files            │
│                   │
├───────────────────┤
│  ⚙ Settings  ›   │  ← click opens menu above
└───────────────────┘
```

### Settings popover

A small floating panel that appears above the Settings button, anchored to the left edge
of the sidebar:

```
┌──────────────────┐
│  Theme        ›  │
└──────────────────┘
```

### Theme submenu

Clicking/hovering "Theme" reveals a nested submenu panel to the right:

```
┌──────────────────┐  ┌──────────────┐
│  Theme        ›  │  │ ✓ Light      │
└──────────────────┘  │   Dark       │
                      │   System     │
                      └──────────────┘
```

The active option has a checkmark (✓). Selecting an option applies it immediately and
closes both menus.

---

## Behaviour

### Theme options

| Option   | Behaviour                                                                 |
|----------|---------------------------------------------------------------------------|
| Light    | Force light mode — adds no class to `<html>`.                             |
| Dark     | Force dark mode — adds `dark` class to `<html>`.                          |
| System   | Reads `prefers-color-scheme` media query; updates if OS preference changes.|

### Persistence

- Preference is saved to `localStorage` under the key `theme`.
- Valid stored values: `"light"`, `"dark"`, `"system"`.
- Default when no stored value exists: `"system"`.

### Applying the theme

- Tailwind is configured with `darkMode: 'class'`.
- The context adds/removes the `dark` class on `document.documentElement` (`<html>`).
- For the `system` option, a `MediaQueryList` listener on
  `prefers-color-scheme: dark` keeps the class in sync with OS changes.

---

## Implementation components

### `src/context/ThemeContext.tsx`

Exports:

```ts
type Theme = 'light' | 'dark' | 'system'

interface ThemeContextValue {
  theme: Theme
  setTheme: (t: Theme) => void
}

export const ThemeProvider: React.FC<{ children: React.ReactNode }>
export function useTheme(): ThemeContextValue
```

Behaviour:
- Reads initial value from `localStorage.getItem('theme')` (default `'system'`).
- On `setTheme`, writes to localStorage and updates the `dark` class on `<html>`.
- On mount (and when `theme === 'system'`), attaches a `matchMedia` listener for
  `(prefers-color-scheme: dark)`.
- Cleans up the listener on unmount or when theme changes away from `'system'`.

### `src/layout/AppShell.tsx`

Changes:
- Remove the `<p className="text-xs text-gray-400">SF Bulk Loader v0.1</p>` footer.
- Add a `SettingsMenu` component (defined in the same file or imported) in its place.

### `SettingsMenu` component

A self-contained component rendered at the bottom of the sidebar:

- Renders a "Settings" button with a gear icon and chevron.
- Clicking toggles a popover that appears **above** the button (positioned with
  `bottom-full` or equivalent CSS).
- The popover contains a "Theme" row with a chevron indicator.
- Clicking "Theme" shows the three theme options.
- Clicking outside closes the popover.
- `useTheme()` provides the current theme and setter.

---

## Tailwind dark mode

`tailwind.config.ts` must include:

```ts
darkMode: 'class',
```

All existing UI components should continue to work in light mode unchanged. Dark-mode
styling can be added to components progressively using `dark:` variants.

---

## Acceptance criteria

- [ ] "SF Bulk Loader v0.1" text is replaced by a Settings button at the bottom of the sidebar.
- [ ] Clicking Settings opens a menu above the button.
- [ ] The menu has a "Theme" item that reveals Light / Dark / System sub-options.
- [ ] Selecting Light removes the `dark` class from `<html>`.
- [ ] Selecting Dark adds the `dark` class to `<html>`.
- [ ] Selecting System mirrors the OS preference and updates dynamically.
- [ ] The active theme is indicated with a checkmark in the submenu.
- [ ] The selection persists across page reloads (via localStorage).
- [ ] Default preference when unset is System.
- [ ] Clicking outside the menu closes it.
