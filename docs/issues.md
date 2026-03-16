# Outstanding Issues

## UI

### Dark mode styling gaps across shared components and pages
Many elements use light-mode Tailwind colour classes without corresponding `dark:` variants, causing poor legibility in dark mode.

**Shared components (affect every page):**
- `Card` — `bg-white`, `border-gray-200`, title `text-gray-900`, subtitle `text-gray-500`
- `Button` — `secondary` variant (`bg-white`, `text-gray-700`, `border-gray-300`); `ghost` variant (`text-gray-600`, `hover:bg-gray-100`)
- `Modal` — `bg-white` panel, `border-gray-200` header/footer, `bg-gray-50` footer, title `text-gray-900`
- `DataTable` — `bg-white` body, `bg-gray-50` thead, `divide-gray-200/100`, cell `text-gray-900`, `hover:bg-gray-50`
- `Badge` — neutral/pending/aborted variants use `bg-gray-100` and gray text
- `EmptyState` — title `text-gray-900`, description `text-gray-500`
- `Progress` — label `text-gray-500`, value `text-gray-700`, track `bg-gray-200`
- `Toast` — all four variants use `bg-white`; message `text-gray-800`
- `Tabs` — `border-gray-200`, inactive tab text and hover colours

**Breadcrumbs:**
- `PlanEditor`, `RunDetail`, `JobDetail` — breadcrumb links use `text-gray-500` / `hover:text-gray-900` / current `text-gray-900` with no dark variants

**Pages with inline gaps:**
- `FilesPage` — file list selected/unselected states, preview table rows, error text
- `Dashboard` — stat card values/labels, table cells
- `Connections` — form inputs (`bg-white`, `border-gray-300`), table cells, test result panels
- `PlansPage` — table cells, error alert
- `PlanEditor` — step cards, preview result panels, form labels, preflight modal
- `RunsPage` — filter inputs, table cells
- `RunDetail` — sticky header `bg-white`, step accordion, stat values
- `JobDetail` — metadata fields, download rows, download button
