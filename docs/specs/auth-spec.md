# Spec: Authentication for the Salesforce Bulk Loader

## Overview

The app currently has **zero authentication**. All API endpoints and frontend routes are publicly accessible to anyone with network access. This spec covers adding user authentication in two delivery stages:

- **Stage 1**: Local username/password authentication with stateless JWT sessions
- **Stage 2**: SAML/SSO with Microsoft Entra ID

Both stages share the same JWT session layer and frontend auth scaffolding. Stage 1 is independently deployable. Stage 2 adds a second login method on top without replacing local accounts.

This document also captures the **Phase 0 decisions** that have already been made so implementation can proceed without ambiguity.

---

## Phase 0 Decisions

The following decisions are fixed unless explicitly changed in a later revision:

| Topic | Decision |
|---|---|
| Login payload | `POST /api/auth/login` accepts **JSON** |
| Phase 1 scope | **Authentication only**. Roles may be stored but are not enforced beyond future admin stories |
| Initial admin bootstrap | **Startup seed** |
| Initial `user` schema | Includes future SAML fields from the start |
| `/api/health` | Remains **public** |
| WebSocket auth | **Protected in Stage 1** |
| Frontend `401` behavior | Redirect to `/login` **only if a session token exists** |
| Missing seed env vars on first boot | **Fail fast** if no users exist |
| Additional user management | **Out of scope** for this change; subsequent user story |
| Refresh tokens | **Out of scope** |
| `initiated_by` value | Store authenticated **username** |
| SAML target | Optimize for **Microsoft Entra ID** first |

---

## Current State

| Layer | Status |
|---|---|
| Backend API auth | None — all endpoints unprotected |
| Frontend routing | All routes unprotected, no login page |
| API client | No `Authorization` header sent |
| User model | None |
| Auth packages (backend) | `python-jose` present (for Salesforce JWT only) |
| Auth packages (frontend) | None |

The `initiated_by` field on `LoadRun` is currently a free-form string and is not tied to a real authenticated identity.

---

## Architecture

### Session Model

Both stages use the same stateless JWT session pattern:

1. User authenticates with either local credentials or SAML
2. Backend issues a signed JWT using `HS256`
3. Frontend stores the token in `localStorage`
4. Frontend injects `Authorization: Bearer <token>` on every protected HTTP request
5. Backend validates the JWT on every protected endpoint via a shared FastAPI dependency
6. If the frontend receives `401` for a request made with an existing token, it clears the session and redirects to `/login`

No server-side session state is maintained. Refresh tokens and token rotation are out of scope for this implementation.

### User Model

A single `user` table supports both authentication methods from the start.

| Column | Type | Used by |
|---|---|---|
| `id` | UUID PK | Both |
| `username` | string, unique, nullable | Local auth |
| `hashed_password` | string, nullable | Local auth |
| `email` | string, nullable | SAML |
| `display_name` | string, nullable | SAML |
| `saml_name_id` | string, nullable | SAML |
| `is_active` | bool, default `true` | Both |
| `role` | string, default `"user"` | Stored only in this story |
| `created_at` | datetime | Both |
| `updated_at` | datetime | Both |

Local users have `username` + `hashed_password`.
SAML users have `saml_name_id` plus SAML-derived profile fields and a null `hashed_password`.

### Token Claims

JWTs should include only stable session claims:

```json
{
  "sub": "<user_id>",
  "username": "alice",
  "role": "user",
  "iat": 1234567000,
  "exp": 1234570600
}
```

Notes:

- `sub` is the canonical user identifier
- `username` is included for convenience and audit readability
- `role` is present for future authorization work but is not enforced in this change

### Auth Scope

This change delivers **authentication**, not full authorization.

- Protected routes require a valid active user
- Roles are stored in the model and token but are not used for route gating yet
- Admin user creation after bootstrap is explicitly deferred

### WebSocket Scope

The run-status WebSocket endpoint is in scope for protection in Stage 1.

Implementation approach:

- Browser connects to `/ws/runs/{run_id}?token=<jwt>` or equivalent supported pattern
- Backend validates the JWT before accepting the socket
- Invalid or missing token results in immediate rejection

---

## Stage 1: Local Password Authentication

### Dependencies

Add the following backend dependency:

```txt
passlib[bcrypt]
```

`python-multipart` is **not required** because login uses a JSON request body, not OAuth form encoding.

### Backend Design

#### Config additions (`backend/app/config.py`)

```python
jwt_secret_key: str
jwt_algorithm: str = "HS256"
jwt_expiry_minutes: int = 60
admin_username: str | None = None
admin_password: str | None = None
```

`jwt_secret_key` is required in all environments except tests that explicitly override it.

#### User model (`backend/app/models/user.py`)

Add a new SQLAlchemy model matching the schema above and wire it into the model package.

#### Migration (`backend/alembic/versions/0003_add_user_table.py`)

Add the `user` table in a single migration. Because the SAML-related fields are already part of the agreed schema, there is no separate later migration for them.

#### Auth schemas (`backend/app/schemas/auth.py`)

- `LoginRequest(username: str, password: str)`
- `TokenResponse(access_token: str, token_type: str = "bearer", expires_in: int)`
- `UserResponse(id, username, email, display_name, role, is_active)`
- `AuthConfigResponse(saml_enabled: bool)`

#### Auth service / dependency (`backend/app/dependencies.py` or `backend/app/services/auth.py`)

Provide reusable helpers for:

- password hashing
- password verification
- JWT encoding
- JWT decoding
- `get_current_user`
- optional `get_current_user_optional` if needed for mixed endpoints
- WebSocket token extraction and validation

`get_current_user` should:

1. Read a bearer token from the `Authorization` header
2. Decode and validate JWT signature and expiry
3. Load the user by `sub`
4. Reject missing, invalid, expired, or inactive users with `401`

#### Auth router (`backend/app/api/auth.py`)

| Endpoint | Auth required | Description |
|---|---|---|
| `POST /api/auth/login` | No | Verify local credentials from JSON body and return JWT |
| `GET /api/auth/me` | Yes | Return current user |
| `GET /api/auth/config` | No | Return auth config such as `{ "saml_enabled": false }` in Stage 1 |
| `POST /api/auth/logout` | No-op | Optional convenience endpoint; client-side logout still clears session locally |

#### Protect existing routers

Apply `Depends(get_current_user)` to all API routers **except**:

- `auth` routes that must remain public
- `/api/health`, which remains public by decision

This includes:

- connections
- load plans
- load steps
- load runs
- jobs
- file listing and preview endpoints

#### Startup admin seed

On application startup:

1. Check whether any users exist
2. If users exist, do nothing
3. If no users exist:
   - require both `ADMIN_USERNAME` and `ADMIN_PASSWORD`
   - fail startup if either is missing
   - create a single active admin user with a hashed password

Requirements:

- idempotent
- no password reseeding after the first user exists
- clear startup error message when bootstrap env vars are missing

#### `initiated_by`

When starting a load run, populate `LoadRun.initiated_by` from the authenticated user's `username`. The request body should no longer be the source of truth for this field.

#### WebSocket protection

Protect `/ws/runs/{run_id}` with token validation in Stage 1. The socket should not accept anonymous connections.

### Frontend Design

#### `AuthContext` (`frontend/src/context/AuthContext.tsx`)

State:

- `token: string | null`
- `user: UserResponse | null`
- `isBootstrapping: boolean`
- `login(token: string): Promise<void>`
- `logout(): void`

Behavior:

- On mount, read token from `localStorage`
- If token exists, call `GET /api/auth/me`
- If that request fails with `401`, clear local session
- `login(token)` stores the token, fetches `/api/auth/me`, and updates state
- `logout()` clears local state and `localStorage`, then navigates to `/login`

#### API client changes (`frontend/src/api/client.ts`)

- Read token from `localStorage` and inject `Authorization` for protected requests
- On `401`, clear the stored token and redirect to `/login` **only if a token was present**
- Avoid redirect loops for `/login` itself and bootstrap checks

#### Login page (`frontend/src/pages/Login.tsx`)

- Username + password form
- Sends JSON to `POST /api/auth/login`
- On success, stores the token via `AuthContext`
- Redirects to `next` if provided, otherwise `/`
- Shows inline error state on bad credentials

#### Route protection

Add `ProtectedRoute` to:

- wait for auth bootstrap to complete
- redirect anonymous users to `/login?next=<current-path>`
- avoid redirecting while the initial `me` request is still in flight

#### App shell updates

Add authenticated-user presentation to the shell:

- show `user.display_name ?? user.username`
- expose logout action

### Testing

Add focused auth tests and update existing backend/frontend tests to support authenticated execution.

Backend additions:

- `backend/tests/test_auth.py`
- shared auth fixtures for creating users and generating tokens
- dependency override strategy for protected route tests

Frontend additions:

- auth context tests
- protected route tests
- login page tests
- API client unauthorized handling tests

Regression expectation:

- most existing API tests will need an authenticated client fixture once route protection is enabled

---

## Stage 2: SAML/SSO with Microsoft Entra ID

Stage 2 adds Entra ID SAML as a second login method. Local username/password accounts remain available as fallback access for bootstrap and break-glass use.

### Dependency

```txt
python3-saml
```

### Backend Design

#### Config additions (`backend/app/config.py`)

```python
saml_sp_entity_id: str | None = None
saml_idp_metadata_url: str | None = None
saml_sp_cert: str | None = None
saml_sp_key: str | None = None
frontend_base_url: str | None = None
```

SAML is considered enabled only when the required SAML settings are present and valid.

#### SAML endpoints (`backend/app/api/auth.py`)

| Endpoint | Description |
|---|---|
| `GET /api/auth/saml/login` | Generate SAML `AuthnRequest` and redirect to Entra ID |
| `POST /api/auth/saml/acs` | Validate assertion, JIT-provision user, issue JWT, redirect to frontend |
| `GET /api/auth/saml/metadata` | Serve SP metadata XML |
| `GET /api/auth/config` | Returns `{ "saml_enabled": true }` when configured |

#### ACS token delivery

After successful SAML validation, redirect to:

```txt
/login#token=<jwt>
```

The frontend reads the fragment, stores the token, and clears the hash. This avoids putting the token in the query string.

#### JIT provisioning

On first SAML login:

1. Find user by `saml_name_id`
2. If absent, create a new active user with:
   - `saml_name_id`
   - `email`
   - `display_name`
   - `role="user"`
3. Issue JWT

#### Entra ID assumptions

Optimize the implementation and docs for Microsoft Entra ID first:

- enterprise application setup
- metadata URL import
- `email` and `displayName` attribute mapping
- ACS and entity ID examples aligned to Entra terminology

AD FS may be supported later, but Entra is the primary target for this spec.

### Frontend Design

Enhance the existing login page:

- fetch `GET /api/auth/config`
- if `saml_enabled`, show a "Sign in with Microsoft" button
- on mount, detect `#token=...` and complete login via `AuthContext`

No other route/auth scaffolding changes are required in Stage 2.

---

## Security Considerations

| Concern | Mitigation |
|---|---|
| JWT secret leaked | Required env var; rotation invalidates existing sessions |
| Weak bootstrap password | Enforce minimum password rules before creating the seeded admin |
| Token storage | `localStorage` is accepted for this app's threat model |
| Anonymous health checks | `/api/health` remains public intentionally for deployment health monitoring |
| Anonymous WebSocket access | WebSocket endpoint requires JWT validation in Stage 1 |
| SAML assertion replay | Rely on library validation and confirm required replay protections during implementation |
| Startup misconfiguration | Fail fast on first boot if no users exist and bootstrap env vars are absent |
| Test bypass | Keep `dependency_overrides` test-only and avoid hidden auth disable switches in production |

---

## Environment Variables

### Stage 1

| Variable | Required | Description |
|---|---|---|
| `JWT_SECRET_KEY` | Yes | Secret used to sign app JWTs |
| `JWT_EXPIRY_MINUTES` | No | Token lifetime; default `60` |
| `ADMIN_USERNAME` | Yes on first boot | Seed admin username |
| `ADMIN_PASSWORD` | Yes on first boot | Seed admin password |

### Stage 2

| Variable | Required | Description |
|---|---|---|
| `SAML_SP_ENTITY_ID` | Yes when SAML enabled | SP entity ID |
| `SAML_IDP_METADATA_URL` | Yes when SAML enabled | Entra metadata URL |
| `SAML_SP_CERT` | Yes when SAML enabled | SP certificate |
| `SAML_SP_KEY` | Yes when SAML enabled | SP private key |
| `FRONTEND_BASE_URL` | Yes when SAML enabled | Frontend URL used for post-login redirects |

---

## Implementation Tickets

These tickets are intended to be small enough to execute incrementally while still producing coherent checkpoints. They are ordered and dependency-aware so you can hand them to Claude one at a time.

### 1. Add Backend User Model and Auth Configuration

Goal: create the persistence and configuration foundation for authentication.

Scope:

- add `User` SQLAlchemy model
- export the model through the backend model package if needed
- add auth-related config settings to `backend/app/config.py`
- update `.env.example` or related env documentation references if they live in-repo
- add Alembic migration `0003_add_user_table.py`

Notes:

- include SAML-related columns in the initial schema
- include `updated_at` in the model and migration
- keep this ticket storage-only; no auth routes yet

Dependencies:

- none

Exit criteria:

- database migrates successfully
- application imports cleanly with the new model/config

### 2. Add Backend Password and JWT Utilities

Goal: create reusable auth primitives before wiring any routes.

Scope:

- add password hashing and verification helpers
- add JWT encode/decode helpers
- add token payload model or helper types if useful
- add `get_current_user` dependency
- add optional WebSocket token validation helper
- add unit tests for hashing and token validation behavior

Notes:

- use JSON login semantics, not OAuth form helpers
- token claims should include `sub`, `username`, `role`, `iat`, and `exp`
- reject inactive users

Dependencies:

- Ticket 1

Exit criteria:

- helpers are independently testable
- valid tokens resolve active users
- invalid, expired, or malformed tokens fail with `401`

### 3. Add Auth API Endpoints

Goal: expose local login/session inspection endpoints without yet protecting the rest of the app.

Scope:

- add `backend/app/api/auth.py`
- implement `POST /api/auth/login`
- implement `GET /api/auth/me`
- implement `GET /api/auth/config`
- add optional no-op `POST /api/auth/logout` if desired
- register the auth router in `backend/app/main.py`
- add backend route tests for login and `me`

Notes:

- `POST /api/auth/login` accepts JSON body
- `GET /api/auth/config` must return at least `saml_enabled`
- auth router remains public

Dependencies:

- Ticket 2

Exit criteria:

- seeded or test-created local user can log in
- `me` returns the correct user for a valid token

### 4. Implement Startup Admin Seed

Goal: make a fresh environment usable without manual database editing.

Scope:

- add startup bootstrap logic
- if zero users exist, require `ADMIN_USERNAME` and `ADMIN_PASSWORD`
- create a single active admin user with hashed password
- fail startup with a clear error if first-boot credentials are missing
- add tests for first-boot success and misconfiguration failure paths

Notes:

- bootstrap must be idempotent
- never overwrite existing users
- additional user management remains out of scope

Dependencies:

- Ticket 3

Exit criteria:

- brand-new database starts with one admin when env vars are present
- startup fails fast when first-boot env vars are missing

### 5. Protect Existing REST API Routes

Goal: require authentication across the existing backend API while keeping health checks public.

Scope:

- apply `Depends(get_current_user)` to protected routers
- keep `/api/health` public
- protect connections, load plans, load steps, runs, jobs, and file endpoints
- decide route-by-route whether helper endpoints in `utility.py` are protected or public per spec
- update backend API tests to use authenticated fixtures or dependency overrides

Notes:

- this ticket should not include WebSocket protection yet
- existing tests in `backend/tests` will need shared authenticated client support

Dependencies:

- Ticket 4

Exit criteria:

- anonymous access to protected REST endpoints returns `401`
- authenticated requests continue to pass existing functional tests

### 6. Tie Load Run Identity to Authenticated User

Goal: make run audit information derive from the authenticated session.

Scope:

- update `POST /api/load-plans/{id}/run` to source `initiated_by` from `current_user.username`
- stop treating request payload input as the source of `initiated_by`
- update affected schemas/tests as needed

Notes:

- store `username`, not display name or user id

Dependencies:

- Ticket 5

Exit criteria:

- new runs record the authenticated username in `initiated_by`

### 7. Protect the Run WebSocket Endpoint

Goal: close the remaining anonymous real-time access path.

Scope:

- validate JWT for `/ws/runs/{run_id}`
- reject missing/invalid token before accepting the socket
- decide and implement token transport format, with query param as the default path
- add backend tests for accepted and rejected WebSocket connections

Notes:

- current frontend primarily polls, so this is mostly backend hardening and future-proofing

Dependencies:

- Ticket 2

Exit criteria:

- anonymous socket connections are rejected
- valid token connections are accepted

### 8. Add Frontend Auth Context and Session Bootstrap

Goal: introduce shared client-side session state before changing routing.

Scope:

- add `AuthContext`
- add token persistence in `localStorage`
- bootstrap `GET /api/auth/me` on app load when a token exists
- expose `login()` and `logout()`
- add frontend tests for bootstrapping, login state, and logout

Notes:

- include `isBootstrapping` or equivalent state to avoid route flicker

Dependencies:

- Ticket 3

Exit criteria:

- frontend can restore an existing session on page refresh
- invalid stored token is cleared cleanly

### 9. Update Frontend API Client for Auth Headers and 401 Handling

Goal: make all API calls auth-aware.

Scope:

- inject `Authorization: Bearer <token>` from local storage or auth context
- on `401`, clear local session and redirect to `/login` only if a token existed
- avoid redirect loops for login/bootstrap flows
- add tests covering authenticated requests and unauthorized responses

Notes:

- keep behavior aligned with the Phase 0 decision: redirect only if a session existed

Dependencies:

- Ticket 8

Exit criteria:

- authenticated requests send bearer tokens
- expired sessions are cleared without noisy redirect behavior for anonymous users

### 10. Add Login Page and Protected Routing

Goal: enforce authentication in the browser and make sign-in possible end-to-end.

Scope:

- add `frontend/src/pages/Login.tsx`
- add `frontend/src/components/ProtectedRoute.tsx`
- update `frontend/src/App.tsx` routing
- preserve `next` redirect behavior
- add route protection tests

Notes:

- login uses JSON request body
- `/login` remains public

Dependencies:

- Ticket 9

Exit criteria:

- anonymous users are redirected to `/login`
- successful login returns the user to the intended destination

### 11. Add User Identity and Logout Controls to the Shell

Goal: surface authenticated state in the main application chrome.

Scope:

- update `AppShell` to display `user.display_name ?? user.username`
- add logout control wired to `AuthContext`
- ensure logout returns the user to `/login`
- add/update UI tests around shell behavior

Dependencies:

- Ticket 10

Exit criteria:

- authenticated user identity is visible in the UI
- logout consistently clears session state

### 12. Stage 1 Hardening and Regression Pass

Goal: stabilize the completed local-auth implementation for deployment.

Scope:

- add or update env documentation
- add bootstrap misconfiguration tests
- enforce bootstrap password minimum rules
- review all backend and frontend auth tests for duplication and fixture quality
- run a regression pass over protected workflows

Notes:

- this is the checkpoint where Stage 1 should be considered deployable

Dependencies:

- Tickets 1 through 11

Exit criteria:

- Stage 1 is documented, tested, and deployable
- no known anonymous access gaps remain in HTTP or WebSocket paths

### 13. Add SAML Readiness Hooks

Goal: prepare the Stage 1 codebase so SAML can be added without structural rework.

Scope:

- ensure `GET /api/auth/config` exposes stable auth capability flags
- extend `Login` page to conditionally render alternate auth options based on config
- add frontend token-fragment handling hook for future `/login#token=...`
- add backend config validation helpers for SAML enablement

Notes:

- this ticket should not yet add the SAML library or actual SAML endpoints

Dependencies:

- Ticket 12

Exit criteria:

- login page and backend contracts are ready for a second login method

### 14. Add Backend Entra SAML Flow

Goal: add SAML login to the backend on top of the existing JWT session model.

Scope:

- add `python3-saml`
- add SAML config settings
- implement `GET /api/auth/saml/login`
- implement `POST /api/auth/saml/acs`
- implement `GET /api/auth/saml/metadata`
- JIT-provision users from SAML assertions
- add backend tests for SAML config and ACS flow components as practical

Notes:

- optimize docs and implementation assumptions for Microsoft Entra ID
- retain local auth for bootstrap and break-glass access

Dependencies:

- Ticket 13

Exit criteria:

- Entra-authenticated users can receive app JWTs through the ACS flow

### 15. Add Frontend Microsoft Sign-In Flow

Goal: complete the browser-side portion of Entra SAML sign-in.

Scope:

- add "Sign in with Microsoft" action to `Login`
- use `auth/config` to conditionally show the button
- consume `#token=...` fragment on return from ACS
- complete session establishment through `AuthContext`
- add frontend tests for SAML-enabled login behavior

Dependencies:

- Ticket 14

Exit criteria:

- Entra sign-in works end-to-end from login page through authenticated app session

### 16. Stage 2 Documentation and Validation

Goal: finalize the SAML feature for rollout.

Scope:

- add Entra-focused setup documentation
- document required metadata, ACS URL, entity ID, and certificate handling
- run regression coverage for both local and SAML login methods
- verify fallback local admin access still works

Dependencies:

- Ticket 15

Exit criteria:

- both login methods are documented and validated
- SAML rollout can proceed without undermining local emergency access

---

## Suggested Work Split

### Agent A: Backend

- user model and migration
- auth services and dependencies
- auth API routes
- startup seed
- route protection
- WebSocket protection
- backend tests

### Agent B: Frontend

- auth context
- API client token handling
- login page
- protected routing
- app shell user/logout UI
- frontend tests

Recommended coordination checkpoints:

1. After Phase 1 backend contract completion
2. After Phase 2 backend protection rollout
3. After Phase 4 frontend route protection
4. After Phase 7 SAML delivery

---

## Files Affected

### Stage 1

| File | New / Modified |
|---|---|
| `backend/app/models/user.py` | New |
| `backend/app/schemas/auth.py` | New |
| `backend/app/api/auth.py` | New |
| `backend/app/dependencies.py` and/or `backend/app/services/auth.py` | New |
| `backend/alembic/versions/0003_add_user_table.py` | New |
| `backend/tests/test_auth.py` | New |
| `backend/app/config.py` | Modified |
| `backend/requirements.txt` | Modified |
| `backend/app/main.py` | Modified |
| `backend/app/api/*.py` | Modified to add auth protection where required |
| `frontend/src/context/AuthContext.tsx` | New |
| `frontend/src/components/ProtectedRoute.tsx` | New |
| `frontend/src/pages/Login.tsx` | New |
| `frontend/src/api/client.ts` | Modified |
| `frontend/src/App.tsx` | Modified |
| `frontend/src/layout/AppShell.tsx` | Modified |
| `frontend/src/main.tsx` | Modified |

### Stage 2

| File | New / Modified |
|---|---|
| `backend/app/api/auth.py` | Modified to add SAML routes |
| `backend/app/config.py` | Modified to add SAML config |
| `backend/requirements.txt` | Modified to add `python3-saml` |
| `frontend/src/pages/Login.tsx` | Modified to add Microsoft sign-in flow |
| SAML-specific docs | Modified |
