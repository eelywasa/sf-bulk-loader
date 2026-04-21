"""Permission key vocabulary for the profile-based RBAC model (spec §5.1).

Keys are plain module-level strings — simpler than an Enum for DB storage
and JSON serialisation. ALL_PERMISSION_KEYS is the authoritative set used by
the startup check in main.py to catch typos in seed data or hand-edits.

Do NOT add require_permission() here — that is SFBL-195's scope.
"""

# Connection permissions
CONNECTIONS_VIEW = "connections.view"
CONNECTIONS_VIEW_CREDENTIALS = "connections.view_credentials"
CONNECTIONS_MANAGE = "connections.manage"

# Plan permissions
PLANS_VIEW = "plans.view"
PLANS_MANAGE = "plans.manage"

# Run permissions
RUNS_VIEW = "runs.view"
RUNS_EXECUTE = "runs.execute"
RUNS_ABORT = "runs.abort"

# File permissions
FILES_VIEW = "files.view"
FILES_VIEW_CONTENTS = "files.view_contents"

# Admin permissions
USERS_MANAGE = "users.manage"
SYSTEM_SETTINGS = "system.settings"

ALL_PERMISSION_KEYS: frozenset[str] = frozenset(
    {
        CONNECTIONS_VIEW,
        CONNECTIONS_VIEW_CREDENTIALS,
        CONNECTIONS_MANAGE,
        PLANS_VIEW,
        PLANS_MANAGE,
        RUNS_VIEW,
        RUNS_EXECUTE,
        RUNS_ABORT,
        FILES_VIEW,
        FILES_VIEW_CONTENTS,
        USERS_MANAGE,
        SYSTEM_SETTINGS,
    }
)
