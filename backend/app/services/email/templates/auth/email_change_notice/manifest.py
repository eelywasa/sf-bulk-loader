"""Manifest for auth/email_change_notice template.

Sent to the user's *current* email address when an email change request is
initiated.  The masked target address is included so the user can identify
the requested change and contact support if they did not initiate it.
"""

REQUIRED_CONTEXT: frozenset = frozenset({"user_display_name", "new_email_masked"})
SUBJECT_CONTEXT: frozenset = frozenset()
