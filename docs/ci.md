# CI Reference

This project uses GitHub Actions with four purpose-built workflows. Each targets a specific
distribution or quality concern and is scoped to fire only when the relevant code is likely
to have changed.

---

## Workflow overview

| Workflow | File | Trigger | Runner |
|---|---|---|---|
| Shared Quality Checks | `ci-shared.yml` | Every push and PR (all branches) | ubuntu-latest |
| Docker Distribution | `ci-docker.yml` | Push/PR targeting `main` | ubuntu-latest |
| Electron Desktop CI | `ci-electron.yml` | Push/PR targeting `main` | macos-latest (smoke + PyInstaller check on main) |
| AWS Skeleton Validation | `ci-aws-skeleton.yml` | Push/PR targeting `main` | ubuntu-latest |
| Release | `release.yml` | Semantic version tags (`v*.*.*`) | ubuntu-latest + matrix: mac-arm64, windows-x64, linux-x64 |

---

## ci-shared.yml — Shared Quality Checks

Runs on every push to any branch and every pull request. This is the primary gate that all
branches must pass.

### Jobs

#### `backend-test` (matrix: sqlite, postgres)

Runs the full pytest suite against both database engines in parallel.

- Python 3.12, pip cache keyed on `backend/requirements.txt`
- PostgreSQL 16 service container always started; connected only when `TEST_DATABASE_URL` is set
- `ENCRYPTION_KEY` is set to a non-empty placeholder — `conftest.py` generates a real Fernet
  key at test time; the placeholder satisfies Pydantic Settings validation at import
- Matrix entries:

  | `db` | `TEST_DATABASE_URL` |
  |---|---|
  | `sqlite` | _(empty — uses default in-memory SQLite)_ |
  | `postgres` | `postgresql+asyncpg://postgres:postgres@localhost:5432/bulk_loader_test` |

#### `frontend-build`

Validates the React/TypeScript frontend compiles cleanly.

- Node 20, npm cache keyed on `frontend/package-lock.json`
- Steps: `npm ci` → `npm run typecheck` (tsc --noEmit) → `npm run build`

#### `config-validate`

Verifies that the three distribution profiles enforce their constraints at import time.

Each check is a separate `python -c` process (fresh module load, no env var bleed-through):

| Profile | Expected behaviour |
|---|---|
| `self_hosted` | `auth_mode=local`, `transport_mode=http` |
| `desktop` | `auth_mode=none`, `transport_mode=local` |
| `aws_hosted` | Raises `ValidationError` containing "PostgreSQL" when `DATABASE_URL` is unset (SQLite default) |

---

## ci-docker.yml — Docker Distribution

Runs on push to `main` and on PRs targeting `main`. Builds the full Docker Compose stack and
runs smoke tests in both supported database modes.

### Jobs

#### `smoke-sqlite`

1. Creates host directories `data/input`, `data/output`, `data/db`
2. Writes a minimal `.env`:
   ```
   APP_DISTRIBUTION=self_hosted
   ```
   `ENCRYPTION_KEY` is intentionally omitted — the backend auto-generates it to
   `/data/db/encryption.key` on first start (the `data/db` volume is writable).
3. `docker compose up -d --build --wait --timeout 120` — blocks until all service healthchecks
   pass (Docker Compose v2 feature)
4. Verifies `GET /api/health` returns `{"status": "ok", ...}`
5. Verifies `GET /api/connections` without a token returns `401` (auth enforced on `self_hosted`)
6. Tears down with `docker compose down -v` (runs even if earlier steps fail)

#### `smoke-postgres`

Same as above but uses the PostgreSQL overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build --wait --timeout 120
```

---

## ci-electron.yml — Electron Desktop CI

Runs on push to `main` and on PRs targeting `main`. Uses a `macos-latest` runner (full macOS VM
with display — no virtual framebuffer required).

### Job: `build-and-smoke`

**Why run from source, not from the packaged `.app`?**
The smoke test uses the dev-mode path in `main.js` (venv uvicorn/alembic), which is faster than
running a full PyInstaller build on every PR. The packaged app with the compiled binary is
validated by the `pyinstaller-check` job on pushes to `main`, and by `release.yml` on tags.

Steps:

1. Build frontend in desktop mode (`VITE_API_URL=http://127.0.0.1:8000`, hash routing, relative
   asset base) — produces `frontend/dist/`
2. Create `backend/.venv` and install requirements — `main.js` dev-mode looks for `.venv/bin/uvicorn`
   and `.venv/bin/alembic` in `BACKEND_DIR`; this ensures they are found
3. `npm install` in `electron/`
4. **Smoke test** — launch `npx electron .` in background, then:
   - Poll `http://127.0.0.1:8000/api/health` for up to 60 seconds
   - Assert `status == "ok"`
   - Assert `GET /api/connections/` returns `200` — confirms desktop profile bypasses auth
   - Kill the Electron process
5. **Package** — `npm run dist -- --mac dir` produces a directory-format `.app` in `electron/dist/`.
   This artifact uses source-bundled backend (no PyInstaller) and is a build-validity check only,
   not a distributable. `CSC_IDENTITY_AUTO_DISCOVERY=false` suppresses keychain lookup.
6. **Upload artifact** — uploaded as `sf-bulk-loader-macos-<sha>`, retained for 1 day.

### Job: `pyinstaller-check` (push to `main` only)

Validates that the PyInstaller binary builds successfully and that the `--migrate` flag works
end-to-end. Runs only on pushes to `main` (not PRs) to keep PR feedback fast.

Steps:

1. Install `requirements-desktop.txt` + PyInstaller
2. `pyinstaller sf_bulk_loader.spec --clean --noconfirm`
3. Run `./dist/sf_bulk_loader/sf_bulk_loader --migrate` with a temp SQLite DB — confirms
   `sys._MEIPASS` path resolution finds the bundled `alembic/` directory and migrations succeed

---

## ci-aws-skeleton.yml — AWS Skeleton Validation

Runs on push to `main` and on PRs targeting `main`. No AWS credentials required — this is a
compile and synthesis check only.

### Job: `cdk-synth`

1. `npm install` in `infrastructure/` (uses `install` not `ci` to handle either lock-file state)
2. `npm run build` — TypeScript compile check via `tsc`
3. `npx cdk synth -c env=staging` — synthesises all four CloudFormation stacks (Network, Data,
   Backend, Frontend) and validates they are structurally sound

---

## release.yml — Release

Triggered only by pushing a semantic version tag:

```bash
git tag v1.2.3
git push origin v1.2.3
```

### Job: `docker-publish`

Builds and pushes both Docker images to GitHub Container Registry (GHCR):

| Image | Tags pushed |
|---|---|
| `ghcr.io/OWNER/sf-bulk-loader-backend` | `1.2.3`, `latest` |
| `ghcr.io/OWNER/sf-bulk-loader-frontend` | `1.2.3`, `latest` |

Authentication uses `GITHUB_TOKEN` (automatically available — no secrets to configure).
Images appear under the repository's **Packages** tab on GitHub.

### Job: `electron-release` (matrix: 3 platforms)

Builds a platform-native, self-contained Electron package on each runner:

| Runner | Output |
|---|---|
| `macos-latest` (arm64) | `.zip` containing unsigned `.app` |
| `windows-latest` | `.exe` NSIS installer |
| `ubuntu-latest` | `.AppImage` |

Each runner: installs `requirements-desktop.txt` + PyInstaller → compiles the backend to a
standalone binary (`pyinstaller sf_bulk_loader.spec`) → builds frontend in desktop mode →
runs `electron-builder` → attaches the output to the GitHub Release via `softprops/action-gh-release`.

The resulting packages are **fully self-contained**: no Python installation is required on the
user's machine.

Release artifacts attach to GitHub Releases storage (not the Actions artifact pool). On public
repos, GitHub Releases storage is unlimited. On private repos it counts against Git LFS storage
(1 GB free, then paid data packs).

---

## For developers

### Running CI checks locally

Before pushing, run the same checks CI will run:

```bash
# Backend tests (SQLite)
cd backend && pytest -x -q

# Backend tests (PostgreSQL — requires a running postgres instance)
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/test_db \
  cd backend && pytest -x -q

# Frontend
cd frontend && npm run typecheck && npm run build

# Config profile validation (same as config-validate job)
cd backend
python -c "
import os
os.environ.update({'APP_DISTRIBUTION': 'self_hosted', 'ENCRYPTION_KEY': 'dGVzdA==', 'JWT_SECRET_KEY': 'test'})
from app.config import settings
assert settings.auth_mode == 'local' and settings.transport_mode == 'http'
print('self_hosted: OK')
"

# CDK synth
cd infrastructure && npm run build && npx cdk synth -c env=staging
```

### Inspecting a Docker smoke test locally

```bash
mkdir -p data/input data/output data/db
echo "APP_DISTRIBUTION=self_hosted" > .env
docker compose up --build --wait --timeout 120
curl http://localhost/api/health
```

### Downloading a CI-built Electron artifact

The CI smoke test uploads a `sf-bulk-loader-macos-<sha>` artifact (retained 1 day). This is a
**source-bundled build** for debugging, not a distributable — it requires Python on the host.

For a properly self-contained build, download release artifacts from the GitHub Releases page
(created when a version tag is pushed). Three platform downloads are available per release:
`mac-arm64` (`.zip`), `windows-x64` (`.exe`), `linux-x64` (`.AppImage`).

### Creating a release

```bash
git tag v1.2.3
git push origin v1.2.3
```

The `release.yml` workflow fires automatically, pushes Docker images to GHCR, and attaches the
Electron `.zip` to a GitHub Release. No manual steps required.

### Trigger summary

| What you're working on | Workflows that will run |
|---|---|
| Any branch (feature, fix, etc.) | `ci-shared.yml` only |
| PR targeting `main` | `ci-shared.yml` + `ci-docker.yml` + `ci-electron.yml` + `ci-aws-skeleton.yml` |
| Push to `main` | All four non-release workflows |
| Semantic version tag | `release.yml` only |

### Required GitHub secrets

No secrets are required for normal CI. The release workflow uses `GITHUB_TOKEN` (built-in) for
GHCR authentication.

For future macOS code signing (currently deferred), the following secrets will be needed:

| Secret | Purpose |
|---|---|
| `MACOS_CERTIFICATE_P12` | Base64-encoded Developer ID Application certificate |
| `MACOS_CERTIFICATE_PASSWORD` | Password for the `.p12` |
| `APPLE_ID` | Apple ID for notarization |
| `APPLE_APP_SPECIFIC_PASSWORD` | App-specific password for notarization |
| `APPLE_TEAM_ID` | Apple Developer team ID |
