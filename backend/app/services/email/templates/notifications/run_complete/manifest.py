"""Manifest for notifications/run_complete template."""

REQUIRED_CONTEXT: frozenset = frozenset({
    "plan_name",
    "run_id",
    "status",
    "total_rows",
    "success_rows",
    "failed_rows",
    "started_at",
    "ended_at",
    "run_url",
})
SUBJECT_CONTEXT: frozenset = frozenset({"plan_name", "status"})
