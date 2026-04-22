"""Manifest for auth/invitation template."""

REQUIRED_CONTEXT: frozenset = frozenset({"user_display_name", "accept_url", "expires_in_hours"})
SUBJECT_CONTEXT: frozenset = frozenset()
