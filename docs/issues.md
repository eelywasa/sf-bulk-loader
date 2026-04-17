# Outstanding Issues

## UI

### ~~Dark mode styling gaps across shared components and pages~~ ✅ RESOLVED (SFBL-15 / SFBL-124, 2026-04-17)

Full theming refactor completed under SFBL-15. All shared components, breadcrumbs, and pages
were migrated to the semantic token system. The final residual sites in
`RunLogDownloadModal.tsx` were cleaned up under SFBL-124. Enumeration removed — verified
clean by `grep -rE 'bg-white|bg-gray-[0-9]+|text-gray-[0-9]+|border-gray-[0-9]+' frontend/src`.

---

### ~~Duplicate CTA on empty Connections page~~ ✅ RESOLVED

When the Connections page has no Salesforce connections or no input file connections, an
empty-state CTA button is shown in the centre of the table area alongside the persistent
"Add Connection" button in the top-right header. The two buttons trigger the same action,
making the centre CTA redundant. Remove the empty-state CTA and rely solely on the
header button, which is always visible regardless of state.

**Affects:** Connections page, both sections (Salesforce connections, Input file connections)

---

### ~~Table overflows viewport horizontally; action buttons inaccessible~~ ✅ RESOLVED

**Affects:** Connections page (likely all pages that render `DataTable` with many/wide columns)
**Distributions:** Both Electron and self-hosted

#### Symptom

When a table's content is wider than the viewport, the table extends off-screen horizontally.
The action buttons at the right end of each row (Test / Edit / Delete) are invisible and
unreachable. No horizontal scrollbar appears.

#### Root Cause

A constraint conflict in the flex layout chain in `AppShell.tsx`:

```
<div class="flex h-screen overflow-hidden">              ← viewport root
  <aside>…</aside>
  <div class="flex-1 flex flex-col overflow-hidden">     ← AppShell.tsx:178
    <header>…</header>
    <main class="flex-1 overflow-auto">                  ← AppShell.tsx:198
      <div class="p-6 space-y-6">                        ← page wrapper (e.g. Connections.tsx)
        <DataTable>
          <div class="overflow-x-auto">                  ← DataTable.tsx:32
            <table class="min-w-full">…</table>
          </div>
        </DataTable>
      </div>
    </main>
  </div>
</div>
```

The page content `<div class="p-6 space-y-6">` is a block child of `<main>`. Without
`min-w-0` or an explicit width constraint, it can grow wider than its flex parent. This
means the `DataTable`'s `overflow-x-auto` wrapper also grows to match the table's intrinsic
width rather than forming a scroll boundary — the table never overflows its container, so
no scrollbar appears. Instead the table pushes the page content wider than the viewport and
is clipped by the ancestor `overflow-hidden`.

#### Relevant files

| File | Line | Detail |
|------|------|--------|
| `frontend/src/layout/AppShell.tsx` | 178 | `overflow-hidden` on main content wrapper clips horizontal overflow |
| `frontend/src/layout/AppShell.tsx` | 198 | `<main>` has `overflow-auto` but is unconstrained in width |
| `frontend/src/components/ui/DataTable.tsx` | 32 | `overflow-x-auto` wrapper — correct intent but ineffective without width constraint on ancestors |
| `frontend/src/pages/Connections.tsx` | ~534 | Page wrapper `<div class="p-6 space-y-6">` has no `min-w-0` |

#### Fix direction

Add `min-w-0` to the `<main>` element in `AppShell.tsx` (line 198) so it cannot grow beyond
its flex parent's bounds. This allows the DataTable's `overflow-x-auto` wrapper to correctly
form the horizontal scroll boundary:

```tsx
<main className="flex-1 overflow-auto min-w-0">
```

The action column in pages like `Connections.tsx` already uses `whitespace-nowrap` on its
cells, which is correct — no change needed there.
