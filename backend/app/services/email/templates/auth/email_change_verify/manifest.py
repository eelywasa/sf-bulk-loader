"""Manifest for auth/email_change_verify template."""

REQUIRED_CONTEXT: frozenset = frozenset({"user_display_name", "confirm_url", "new_email", "expires_in_minutes"})
SUBJECT_CONTEXT: frozenset = frozenset()
