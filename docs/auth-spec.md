# Spec: Authentication for the Salesforce Bulk Loader

## Overview

The app currently has **zero authentication**. All API endpoints and frontend routes are publicly accessible to anyone with network access. This spec covers adding user authentication in two phases:

- **Phase 1** — Local username/password auth with JWT sessions
- **Phase 2** — SAML/SSO with Active Directory (AD FS or Azure AD / Entra ID)

Both phases share the same JWT session layer and frontend auth scaffolding. Phase 1 can be deployed independently; Phase 2 adds a second login method on top without replacing Phase 1.

---

## Current State

| Layer | Status |
|---|---|
| Backend API auth | None — all endpoints unprotected |
| Frontend routing | All routes unprotected, no login page |
| API client | No `Authorization` header sent |
| User model | None |
| Auth packages (backend) | `python-jose` present (Salesforce use only) |
| Auth packages (frontend) | None |

The `initiated_by` field on `LoadRun` is a free-form string — not tied to a real user identity.

---

## Architecture

### Session Model

Both phases use the same stateless JWT session pattern:

1. User authenticates (password check or SAML assertion)
2. Backend issues a signed JWT (`HS256`, configurable expiry, default 60 min)
3. Frontend stores token in `localStorage`, injects it as `Authorization: Bearer <token>` on every request
4. Backend validates the JWT on every protected endpoint via a shared FastAPI dependency
5. On `401`, frontend clears the token and redirects to `/login`

No server-side session state is maintained. Token refresh is out of scope for Phase 1.

### User Model

A single `user` table supports both auth methods. Columns used depend on the auth method:

| Column | Type | Used by |
|---|---|---|
| `id` | UUID PK | Both |
| `username` | string, unique | Phase 1 |
| `hashed_password` | string, nullable | Phase 1 |
| `email` | string, nullable | Phase 2 (from SAML attributes) |
| `display_name` | string, nullable | Phase 2 (from SAML attributes) |
| `saml_name_id` | string, nullable | Phase 2 |
| `is_active` | bool, default true | Both |
| `role` | string, default `"user"` | Both |
| `created_at` | datetime | Both |

Phase 1 users have `username` + `hashed_password`, null SAML fields.
Phase 2 users (JIT provisioned) have `email` + `saml_name_id`, null `hashed_password`.

### Token Claims

```json
{
  "sub": "<user_id>",
  "username": "alice",
  "role": "user",
  "exp": 1234567890
}
```

---

## Phase 1: Local Password Authentication

### New Dependencies

```
passlib[bcrypt]       # password hashing
python-multipart      # form data parsing (OAuth2PasswordBearer requirement)
```

`python-jose` is already present.

### Backend

#### Config additions (`backend/app/config.py`)

```python
jwt_secret_key: str          # required, no default — must be set in .env
jwt_algorithm: str = "HS256"
jwt_expiry_minutes: int = 60
```

#### User model (`backend/app/models/user.py`)

SQLAlchemy 2.0 `MappedColumn` style, matching existing models.

#### Migration (`backend/alembic/versions/0003_add_user_table.py`)

Adds the `user` table. Schema matches the model above.

#### Auth schemas (`backend/app/schemas/auth.py`)

- `LoginRequest(username: str, password: str)`
- `TokenResponse(access_token: str, token_type: str = "bearer", expires_in: int)`
- `UserResponse(id, username, email, display_name, role, is_active)`

#### Auth router (`backend/app/api/auth.py`)

| Endpoint | Auth required | Description |
|---|---|---|
| `POST /api/auth/login` | No | Verify credentials, return JWT |
| `GET /api/auth/me` | Yes | Return current user from token |
| `POST /api/auth/logout` | No (client-side only) | No server state to clear |

#### Auth dependency (`backend/app/dependencies.py`)

```python
async def get_current_user(
    token: str = Depends(OAuth2PasswordBearer(tokenUrl="/api/auth/login")),
    db: AsyncSession = Depends(get_db),
) -> User:
    # decode JWT, look up user, raise 401 if invalid/expired/inactive
```

Applied to all existing routers:
```python
router = APIRouter(prefix="...", dependencies=[Depends(get_current_user)])
```

The auth router itself is registered **without** this dependency.

#### Admin seed

A startup hook (or CLI script) creates an initial admin user if no users exist, using credentials from env vars `ADMIN_USERNAME` / `ADMIN_PASSWORD`. This ensures the app is usable immediately after first deployment without manual DB surgery.

#### `initiated_by` (`backend/app/api/load_plans.py`)

Populate from the authenticated user on `POST /api/load-plans/{id}/run`.

### Frontend

#### `AuthContext` (`frontend/src/context/AuthContext.tsx`)

Holds `token: string | null`, `user: UserResponse | null`, `login(token)`, `logout()`.

- On mount: reads token from `localStorage`, calls `GET /api/auth/me` to populate `user`
- `login()`: stores token in `localStorage`, sets state
- `logout()`: clears `localStorage`, resets state, navigates to `/login`

#### `client.ts` changes (`frontend/src/api/client.ts`)

- Inject `Authorization: Bearer <token>` header from `localStorage` on every request
- Intercept `401` responses: clear token, redirect to `/login`

#### Login page (`frontend/src/pages/Login.tsx`)

- Username + password form
- Calls `POST /api/auth/login`
- On success: stores token, redirects to intended destination (or `/` if none)
- Shows inline error on failure

#### `ProtectedRoute` (`frontend/src/components/ProtectedRoute.tsx`)

Checks for token presence. If absent, redirects to `/login?next=<current-path>`.

#### `App.tsx` changes

- `/login` route added, unprotected
- All other routes wrapped in `<ProtectedRoute>`

#### `AppShell` changes (`frontend/src/layout/AppShell.tsx`)

- Display `user.display_name ?? user.username` in sidebar footer
- Logout button that calls `logout()` from `AuthContext`

#### `main.tsx` changes

Wrap `<App>` in `<AuthProvider>`.

### Testing

- Auth dependency overrideable in tests via `app.dependency_overrides`
- New test file `backend/tests/test_auth.py`:
  - `POST /api/auth/login` with valid credentials → `200` + token
  - `POST /api/auth/login` with bad credentials → `401`
  - `GET /api/auth/me` with valid token → `200` + user
  - `GET /api/auth/me` with no token → `401`
  - `GET /api/connections/` with no token → `401`
  - `GET /api/connections/` with valid token → `200`

---

## Phase 2: SAML/SSO with Active Directory

Phase 2 adds SAML as a second login method. Phase 1 local accounts remain active as a fallback for service accounts and emergency access.

### New Dependency

```
python3-saml      # lightweight SP-only SAML library
```

### Backend

#### Config additions (`backend/app/config.py`)

```python
saml_sp_entity_id: str | None = None      # e.g. "https://bulk-loader.internal"
saml_idp_metadata_url: str | None = None  # Azure AD / AD FS metadata endpoint
saml_sp_cert: str | None = None           # PEM certificate (SP signing/encryption)
saml_sp_key: str | None = None            # PEM private key
```

SAML endpoints are only registered if `saml_sp_entity_id` is set, so Phase 1-only deployments are unaffected.

#### SAML endpoints (added to `backend/app/api/auth.py`)

| Endpoint | Description |
|---|---|
| `GET /api/auth/saml/login` | Generate SAML `AuthnRequest`, redirect browser to IdP |
| `POST /api/auth/saml/acs` | Assertion Consumer Service — validate IdP response, JIT-provision user, issue JWT, redirect to frontend with token |
| `GET /api/auth/saml/metadata` | Serve SP metadata XML for AD registration |

#### ACS token delivery

After validating the SAML assertion, the ACS endpoint redirects to:
```
/login#token=<jwt>
```
The frontend reads the fragment and stores the token. This avoids token exposure in server logs while keeping the flow simple.

#### JIT user provisioning

On first SAML login:
1. Look up `User` by `saml_name_id`
2. If not found: create with `email` and `display_name` from assertion attributes, `is_active=True`, `role="user"`
3. Issue JWT for the user

#### SP Certificate

A dedicated X.509 cert/key pair is required for the SAML SP (separate from Salesforce certs). A self-signed cert is sufficient for most IdP configurations. Certificate and key are provided via environment variables or mounted files.

### AD / Infrastructure Setup (outside the app)

- Register the app as an Enterprise Application (Azure AD) or Relying Party Trust (AD FS)
- ACS URL: `https://<host>/api/auth/saml/acs`
- Entity ID: matches `saml_sp_entity_id` config
- Attribute mappings: emit `email`, `displayName` in the assertion
- Import IdP metadata XML URL into `saml_idp_metadata_url`

### Frontend changes

- `Login.tsx` gains a second "Sign in with Active Directory" button (shown only if backend reports SAML is configured — add `GET /api/auth/config` returning `{ saml_enabled: bool }`)
- Token receipt: on mount, `Login.tsx` checks `window.location.hash` for `#token=...`; if found, calls `login(token)` and clears the hash
- All other frontend auth components unchanged

### User model migration

Add `saml_name_id`, `email`, `display_name` columns to the `user` table. `hashed_password` becomes nullable (already planned that way in Phase 1 schema above).

---

## Security Considerations

| Concern | Mitigation |
|---|---|
| JWT secret leaked | Required env var, never committed; rotate by changing `JWT_SECRET_KEY` (invalidates all sessions) |
| Weak passwords | Enforce minimum length on `POST /api/auth/users` (admin user creation) |
| Token storage | `localStorage` is acceptable for this threat model; `httpOnly` cookies would require CSRF protection |
| SAML assertion replay | `python3-saml` validates `NotOnOrAfter` and tracks assertion IDs |
| SP cert compromise | Cert/key injected via env or mounted secret, never in source code |
| Test bypass | `dependency_overrides` is test-only; production `APP_ENV=production` could assert no overrides are set |

---

## Environment Variables

### Phase 1

| Variable | Required | Description |
|---|---|---|
| `JWT_SECRET_KEY` | Yes | Random secret for signing JWTs |
| `JWT_EXPIRY_MINUTES` | No (default 60) | Token lifetime |
| `ADMIN_USERNAME` | Yes (first run) | Seed admin account username |
| `ADMIN_PASSWORD` | Yes (first run) | Seed admin account password |

### Phase 2 (additional)

| Variable | Required | Description |
|---|---|---|
| `SAML_SP_ENTITY_ID` | Yes (if SAML enabled) | SP entity ID URL |
| `SAML_IDP_METADATA_URL` | Yes (if SAML enabled) | IdP metadata endpoint |
| `SAML_SP_CERT` | Yes (if SAML enabled) | PEM certificate |
| `SAML_SP_KEY` | Yes (if SAML enabled) | PEM private key |

---

## Files Affected

### Phase 1

| File | New / Modified |
|---|---|
| `backend/app/models/user.py` | New |
| `backend/app/schemas/auth.py` | New |
| `backend/app/api/auth.py` | New |
| `backend/app/dependencies.py` | New |
| `backend/alembic/versions/0003_add_user_table.py` | New |
| `backend/tests/test_auth.py` | New |
| `backend/app/config.py` | Modified |
| `backend/requirements.txt` | Modified |
| `backend/app/main.py` | Modified |
| `backend/app/api/*.py` (all routers) | Modified — add auth dependency |
| `backend/app/api/load_plans.py` | Modified — populate `initiated_by` |
| `frontend/src/context/AuthContext.tsx` | New |
| `frontend/src/components/ProtectedRoute.tsx` | New |
| `frontend/src/pages/Login.tsx` | New |
| `frontend/src/api/client.ts` | Modified |
| `frontend/src/App.tsx` | Modified |
| `frontend/src/layout/AppShell.tsx` | Modified |
| `frontend/src/main.tsx` | Modified |

### Phase 2 (additions / changes on top of Phase 1)

| File | New / Modified |
|---|---|
| `backend/app/api/auth.py` | Modified — add SAML routes |
| `backend/app/config.py` | Modified — add SAML config |
| `backend/requirements.txt` | Modified — add `python3-saml` |
| `backend/alembic/versions/0004_add_saml_user_fields.py` | New |
| `frontend/src/pages/Login.tsx` | Modified — add SAML button + token receipt |
