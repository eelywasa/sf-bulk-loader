"""Break-glass CLI for the Salesforce Bulk Loader.

Invoke as:
    python -m app.cli <subcommand> [args]

Available subcommands:
    admin-recover <email>   Reset an admin user's password and unblock account.
    unlock <email>          Clear lockout for any user.
    list-admins             Print a table of all admin users.
"""
