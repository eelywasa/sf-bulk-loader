"""SOQL preflight validation via the Salesforce query explain endpoint.

Calls ``GET /services/data/v{version}/query?explain=<soql>`` to check whether
a SOQL statement is syntactically valid and references resolvable fields —
without consuming governor limits (no rows are read).

Response handling:
    200 ⇒ SOQL is valid. The response body contains an ``plans`` array; the
          first plan's summary fields (leadingOperationType, relativeCost,
          sobjectType) are captured and returned.
    400 ⇒ SOQL is invalid. Salesforce returns a user-friendly error array;
          the first message is surfaced verbatim (it is already human-readable).
    5xx / 429 ⇒ transient error; retried with the same backoff policy used
                throughout the SF client layer (see salesforce_bulk.py).

Usage::

    from app.services.salesforce_query_validation import (
        explain_soql,
        SoqlExplainResult,
    )

    result = await explain_soql(instance_url, access_token, soql)
    if result.valid:
        print(result.plan)          # {"leadingOperation": ..., "sobjectType": ...}
    else:
        print(result.error)         # Salesforce error message

# ---- SOQL explain ----
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.config import settings
from app.observability.events import BulkQueryEvent, OutcomeCode
from app.observability.sanitization import sanitize_soql
from app.services.salesforce_bulk import BulkAPIError

logger = logging.getLogger(__name__)


# ── Result types ─────────────────────────────────────────────────────────────


@dataclass
class SoqlExplainResult:
    """Structured outcome of a SOQL explain call.

    Attributes:
        valid: True when Salesforce accepted the SOQL.
        plan:  For valid queries — a dict with at minimum ``leadingOperation``
               and ``sobjectType`` (and optionally ``cost``, ``relativeCost``).
               Empty dict when *valid* is False.
        error: Human-readable Salesforce error string when *valid* is False.
               Empty string when *valid* is True.
    """

    valid: bool
    plan: dict[str, Any] = field(default_factory=dict)
    error: str = ""


# ── Maximum retries must match salesforce_bulk._MAX_RETRIES ──────────────────

_MAX_RETRIES = 3


# ── Core helper ──────────────────────────────────────────────────────────────


async def explain_soql(
    instance_url: str,
    access_token: str,
    soql: str,
    *,
    api_version: Optional[str] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> SoqlExplainResult:
    """Call the Salesforce explain endpoint and return a structured result.

    This function is intentionally *not* a method on ``SalesforceBulkClient``
    to keep the ingest and query validation concerns separate, but it shares
    the same retry/backoff logic.

    Args:
        instance_url: Salesforce My Domain base URL (e.g.
            ``https://myorg.my.salesforce.com``).
        access_token: Valid Bearer token obtained via
            :func:`app.services.salesforce_auth.get_access_token`.
        soql: Raw SOQL string to validate (not URL-encoded — this function
            handles encoding).
        api_version: Overrides ``settings.sf_api_version`` when supplied.
        http_client: Optional pre-existing :class:`httpx.AsyncClient`. When
            *None* a temporary client is created for the duration of the call.

    Returns:
        :class:`SoqlExplainResult` with ``valid=True`` and plan details on
        success, or ``valid=False`` and the Salesforce error message on a 400.

    Raises:
        BulkAPIError: After all retries are exhausted on 5xx / 429 responses.
    """
    version = api_version or settings.sf_api_version
    encoded_soql = quote(soql, safe="")
    url = f"{instance_url.rstrip('/')}/services/data/{version}/query?explain={encoded_soql}"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    async def _do_call(client: httpx.AsyncClient) -> SoqlExplainResult:
        last_response: Optional[httpx.Response] = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = await client.get(url, headers=headers)
            except httpx.RequestError as exc:
                if attempt == _MAX_RETRIES:
                    raise BulkAPIError(
                        f"Network error on GET {url} after {_MAX_RETRIES} retries: {exc}"
                    ) from exc
                wait = float(2**attempt)
                logger.warning(
                    "Network error on explain call (attempt %d/%d), retrying in %.0f s: %s",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    wait,
                    exc,
                    extra={
                        "event_name": BulkQueryEvent.REQUEST_RETRIED,
                        "outcome_code": OutcomeCode.NETWORK_ERROR,
                    },
                )
                await asyncio.sleep(wait)
                continue

            last_response = response

            # ── 200 — valid SOQL ─────────────────────────────────────────────
            if response.status_code == 200:
                body = response.json()
                plans = body.get("plans", [])
                plan_summary: dict[str, Any] = {}
                if plans:
                    first = plans[0]
                    plan_summary = {
                        "leadingOperation": first.get("leadingOperationType", ""),
                        "sobjectType": first.get("sobjectType", ""),
                        "cost": first.get("cost"),
                        "relativeCost": first.get("relativeCost"),
                    }
                logger.debug(
                    "SOQL explain returned valid plan for %s: %s",
                    sanitize_soql(soql),
                    plan_summary,
                    extra={
                        "event_name": BulkQueryEvent.JOB_CREATED,
                        "outcome_code": OutcomeCode.OK,
                    },
                )
                return SoqlExplainResult(valid=True, plan=plan_summary)

            # ── 400 — invalid SOQL (not retriable) ──────────────────────────
            if response.status_code == 400:
                error_msg = _extract_sf_error(response)
                logger.warning(
                    "SOQL explain rejected (400) for %s: %s",
                    sanitize_soql(soql),
                    error_msg,
                    extra={
                        "event_name": BulkQueryEvent.JOB_FAILED,
                        "outcome_code": OutcomeCode.QUERY_SOQL_SYNTAX_REJECTED,
                    },
                )
                return SoqlExplainResult(valid=False, error=error_msg)

            # ── 429 — rate-limited ───────────────────────────────────────────
            if response.status_code == 429:
                if attempt == _MAX_RETRIES:
                    break
                retry_after_raw = response.headers.get("Retry-After", "")
                wait = (
                    float(retry_after_raw)
                    if retry_after_raw.isdigit()
                    else float(2**attempt)
                )
                logger.warning(
                    "Rate-limited (429) on explain (attempt %d/%d), retrying in %.1f s",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    wait,
                    extra={
                        "event_name": BulkQueryEvent.RATE_LIMITED,
                        "outcome_code": OutcomeCode.RATE_LIMITED,
                    },
                )
                await asyncio.sleep(wait)
                continue

            # ── 5xx — server error ───────────────────────────────────────────
            if response.status_code >= 500:
                if attempt == _MAX_RETRIES:
                    break
                wait = float(2**attempt)
                logger.warning(
                    "Server error %d on explain (attempt %d/%d), retrying in %.0f s",
                    response.status_code,
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    wait,
                    extra={
                        "event_name": BulkQueryEvent.REQUEST_RETRIED,
                        "outcome_code": None,
                    },
                )
                await asyncio.sleep(wait)
                continue

            # ── Other non-2xx (3xx auth redirects, etc.) ────────────────────
            raise BulkAPIError(
                f"Unexpected status {response.status_code} from explain endpoint",
                status_code=response.status_code,
                body=response.text,
            )

        # All retries exhausted.
        assert last_response is not None
        raise BulkAPIError(
            f"GET {url} failed after {_MAX_RETRIES} retries: "
            f"HTTP {last_response.status_code}",
            status_code=last_response.status_code,
            body=last_response.text,
        )

    if http_client is not None:
        return await _do_call(http_client)

    async with httpx.AsyncClient(timeout=30.0) as client:
        return await _do_call(client)


# ── Error extraction ─────────────────────────────────────────────────────────


def _extract_sf_error(response: httpx.Response) -> str:
    """Pull the first user-readable message from a Salesforce 400 response.

    Salesforce typically returns a JSON array like::

        [{"message": "INVALID_FIELD: ...", "errorCode": "INVALID_FIELD"}]

    This function returns the ``message`` of the first entry.  If parsing
    fails we fall back to the raw response text (truncated to 500 chars).
    """
    try:
        body = response.json()
        if isinstance(body, list) and body:
            return body[0].get("message", response.text[:500])
        if isinstance(body, dict):
            return body.get("message", response.text[:500])
    except Exception:
        pass
    return response.text[:500]
