# tests/e2e

Playwright end-to-end test suite for sf-bulk-loader. See `docs/specs/e2e-testing-spec.md` for architecture, tier model (D2), and directory layout (D7/D13).

## Quick start

```bash
cd tests/e2e
npm install
npx playwright install chromium
npm run e2e:1a    # Tier 1a — org-free specs (every PR)
npm run e2e:1b    # Tier 1b — fixture-backed metadata specs (every PR)
npm run e2e:2     # Tier 2 — scratch-org specs (nightly + release gate)
```

## Bringing up the local stack

Tier 1a and Tier 1b specs require the application stack running locally. Use Docker Compose from the repo root:

```bash
# Start the full stack (backend + frontend) in no-auth desktop mode
APP_DISTRIBUTION=desktop docker compose up -d

# For Tier 1b: also set SF_DESCRIBE_FIXTURES_DIR (D5)
# SF_DESCRIBE_FIXTURES_DIR="tests/e2e/app/fixtures/describe:tests/e2e/sf/fixtures/describe" \
#   docker compose up -d
```

The frontend is served at `http://localhost:5173` by default (overrideable via `E2E_BASE_URL`).

## Directory layout

```
tests/e2e/
├── sf/          # Salesforce-shaped, app-blind (see sf/README.md)
├── app/         # Bulk-loader-specific (see app/README.md)
├── package.json
└── playwright.config.ts
```

Import direction: `app/` may import from `sf/`; `sf/` must NOT import from `app/` (enforced by ESLint — `npm run lint`).
