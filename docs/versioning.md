# Versioning

## What this covers / who should read this

How version numbers are assigned, where they come from, and how to cut a
release. Read this before pushing a release tag or debugging the version shown
on the **Settings → About** page.

---

## Version format

```
<major>.<minor>.<build>          →  0.8.312      (release)
<major>.<minor>.<build>-dev      →  0.8.312-dev  (interim build)
```

| Component | Who controls it | What it signals |
|---|---|---|
| `major` | You, manually | Breaking change or major milestone |
| `minor` | You, manually | New feature set — bumped by pushing a tag |
| `build` | CI, automatically | `github.run_number` — monotonically increasing within GitHub Actions |
| `-dev` suffix | CI, automatically | Appended on non-tag builds (PRs, branch pushes) |

---

## Source of truth: git tags

`major.minor` is declared by pushing a git tag in the form `v<major>.<minor>`.
CI reads the tag, appends the run number, and produces the full version string.

**No `VERSION` file to maintain.** The tag *is* the declaration.

```bash
# Declare a new minor version — triggers CI to build and publish all distribution targets
git tag v0.9
git push origin v0.9
```

The tag pattern that triggers the release workflow is `v[0-9]+.[0-9]+`
(two-level only). Three-level tags like `v0.9.0` are **not** release triggers.

---

## How CI computes the full version

All workflows that build distributable packages contain a `Compute version` step:

```yaml
- name: Compute version
  id: ver
  run: |
    if [[ "$GITHUB_REF" == refs/tags/v* ]]; then
      BASE="${GITHUB_REF_NAME#v}"          # "0.9"
      VERSION="${BASE}.${{ github.run_number }}"   # "0.9.312"
    else
      LATEST=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0")
      BASE="${LATEST#v}"                   # "0.9"
      VERSION="${BASE}.${{ github.run_number }}-dev"  # "0.9.318-dev"
    fi
    echo "version=${VERSION}" >> $GITHUB_OUTPUT
```

`git describe --tags --abbrev=0` walks back through history to find the most
recent tag, so interim builds always carry the correct `major.minor` rather
than a meaningless `0.0`.

> **Requirement:** all checkout steps in release and CI workflows must use
> `fetch-depth: 0`. A shallow clone cannot walk back to find earlier tags and
> `git describe` will fail.

---

## Where the version is baked in

### Docker images

Three build-time `ARG`s are injected into the backend image:

```
ARG APP_VERSION=0.0.0-dev
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ENV APP_VERSION=$APP_VERSION APP_GIT_SHA=$GIT_SHA APP_BUILD_TIME=$BUILD_TIME
```

These become `ENV` variables readable at runtime via `os.environ`. The
**Settings → About** page reads them from `GET /api/admin/about`.

For **local builds** (outside CI), operators who want accurate provenance can
supply them explicitly:

```bash
GIT_SHA=$(git rev-parse --short HEAD) \
BUILD_TIME=$(date -u +%FT%TZ) \
APP_VERSION=0.9.0-local \
docker compose build
```

Without these, the image shows `0.0.0-dev` / `unknown` — which is a clear
signal that it is a development build, not a release.

### Electron (desktop)

CI stamps the computed version into `electron/package.json` before building:

```bash
npm version 0.9.312 --no-git-tag-version --allow-same-version
```

Electron Builder reads from `package.json`, so the version appears in the
installer filename and the app's built-in About dialog.

---

## `pyproject.toml` — project metadata only

`backend/pyproject.toml` exists to give the backend a proper Python package
identity for tooling (mypy, pytest, future `pip install`). Its `version` field
is a placeholder `0.0.0` and is **never** used for the displayed version.

The displayed version always comes from the `APP_VERSION` environment variable.

---

## Cutting a release — step by step

1. Ensure `main` is in a releasable state (CI green, Codex review done, PR merged).
2. Decide the new `major.minor` (e.g. `0.9`).
3. Push the tag:
   ```bash
   git tag v0.9
   git push origin v0.9
   ```
4. The `release.yml` workflow fires automatically:
   - Builds and pushes Docker images tagged `0.9.<run_number>` and `latest` to GHCR.
   - Builds Electron installers for macOS, Windows, and Linux.
   - Attaches the installers to a GitHub Release.
5. Verify the **Settings → About** page on a freshly-pulled image shows the expected version.

---

## Related

- [Self-Hosted Docker Deployment](deployment/docker.md) — `build.args` for provenance
- [Settings → About page](usage/admin-settings.md) — where operators see the version
- [`backend/pyproject.toml`](../backend/pyproject.toml) — package metadata placeholder
- [`.github/workflows/release.yml`](../.github/workflows/release.yml) — release workflow
