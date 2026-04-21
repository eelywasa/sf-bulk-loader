"""Entry point for ``python -m app.cli``.

Dispatches subcommands via argparse.  Each subcommand is implemented in
:mod:`app.cli.commands` and invoked synchronously via ``asyncio.run``.
"""

import argparse
import sys

from app.cli.commands import cmd_admin_recover, cmd_list_admins, cmd_unlock


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Break-glass administration CLI for the Salesforce Bulk Loader.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<subcommand>")
    sub.required = True

    # admin-recover
    p_recover = sub.add_parser(
        "admin-recover",
        help="Reset an admin user password and unblock the account.",
    )
    p_recover.add_argument("email", help="Email address of the admin user to recover.")

    # unlock
    p_unlock = sub.add_parser(
        "unlock",
        help="Clear login lockout for any user (does not reset password).",
    )
    p_unlock.add_argument("email", help="Email address of the user to unlock.")

    # list-admins
    sub.add_parser(
        "list-admins",
        help="Print a formatted table of all admin users.",
    )

    args = parser.parse_args()

    if args.command == "admin-recover":
        cmd_admin_recover(args.email)
    elif args.command == "unlock":
        cmd_unlock(args.email)
    elif args.command == "list-admins":
        cmd_list_admins()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
