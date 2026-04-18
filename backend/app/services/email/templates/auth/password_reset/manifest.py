"""Manifest for auth/password_reset template."""

REQUIRED_CONTEXT: frozenset = frozenset({"user_display_name", "reset_url", "expires_in_minutes"})
SUBJECT_CONTEXT: frozenset = frozenset()
