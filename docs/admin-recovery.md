# Admin Recovery — Regaining Access When Locked Out

If every admin account becomes inaccessible (forgotten password, all admins
deleted, etc.) you can recover access using the **break-glass CLI** that is
bundled with the backend process.

## Prerequisites

You need shell access to the host or container running the backend.

- **Docker / Docker Compose:** `docker exec -it <container> bash`
- **Desktop (Electron):** open a terminal on the host machine — the binary
  includes the CLI.
- **AWS hosted:** SSH into the EC2 instance or use ECS Exec.

The CLI reads the same database and environment variables as the running server.
Set `DATABASE_URL` and `ENCRYPTION_KEY` in your environment if they are not
already available in the shell.

## Step 1 — List existing admin accounts

```bash
python -m app.cli list-admins
```

This prints a table of all users with `is_admin=True`, their status, and
whether they are currently locked.

## Step 2 — Recover a known admin account

If you know the email address of an admin user (even one that is locked or has
forgotten their password):

```bash
python -m app.cli admin-recover admin@example.com
```

This command:
1. Resets the user's password to a randomly-generated temporary password.
2. Sets `must_reset_password=True` — the user must change it on first login.
3. Unlocks the account and sets status to `active`.
4. Prints the temporary password **once** to stdout — store it securely.

Exit codes:

| Code | Meaning |
|------|---------|
| 0    | Success |
| 2    | No user found for the given email |
| 3    | User is not an admin (`is_admin=False`) |
| 4    | User has been deleted (soft-delete) — create a new account instead |

## Step 3 — Clear a lockout without resetting the password

If an admin account is locked but the password is known:

```bash
python -m app.cli unlock admin@example.com
```

This clears `locked_until`, resets `failed_login_count` to 0, and transitions
the status back to `active` if it was `locked`.

## Best practice — keep at least two admin accounts

The application shows a warning banner on the **User Management** page when
there is only one active admin. Dismiss the banner by promoting a second user
to the `admin` profile.

Recommended setup:
1. Bootstrap admin (set via `ADMIN_EMAIL` env var) — break-glass account, not
   used for day-to-day work.
2. At least one operational admin account tied to a real person's email.

Having two admins means that if one account is locked or inaccessible, the
other can reset it through the UI without needing CLI access.

## Running the CLI in Docker

```bash
# One-shot command (does not require an interactive shell)
docker exec <container-name> python -m app.cli admin-recover admin@example.com

# Interactive shell
docker exec -it <container-name> bash
python -m app.cli list-admins
```

Replace `<container-name>` with the actual container name (e.g. `sfbl-backend`).
Run `docker ps` if you are unsure.
