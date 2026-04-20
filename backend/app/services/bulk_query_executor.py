"""Salesforce Bulk API 2.0 query executor (SFBL-169).

Runs a single bulk-query job for a given SOQL expression and streams the
results to an :class:`~app.services.output_storage.OutputStorage` sink via
the ``open_writer`` streaming API added in SFBL-174.

Job lifecycle
-------------
1. ``POST /services/data/v{api_version}/jobs/query`` — create the query job.
2. Poll ``GET /jobs/query/{id}`` with exponential backoff until terminal.
3. Paginate ``GET /jobs/query/{id}/results`` following ``Sforce-Locator``
   headers until the locator is absent or ``"null"``.
4. Stream each page through :class:`AsyncWritable`, stripping the CSV header
   row on every page after the first.

HTTP retry policy matches :mod:`app.services.salesforce_bulk`:
  - 3 retries with 1 s / 2 s / 4 s backoff on 5xx and 429 responses.
  - 429 honours the ``Retry-After`` header when present.

The caller is responsible for constructing the target relative path; the
recommended convention is ``<run_id>/<seq>-<object>-<timestamp>.csv``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.config import settings
from app.observability.events import BulkQueryEvent, OutcomeCode
from app.observability.metrics import (
    record_bulk_query_job_completed,
    record_bulk_query_job_created,
    record_bulk_query_job_failed,
)
from app.observability.sanitization import sanitize_soql
from app.observability import tracing
from app.services.output_storage import OutputStorage

logger = logging.getLogger(__name__)

# Terminal states for the Bulk API 2.0 query endpoint.
_QUERY_TERMINAL_STATES: frozenset[str] = frozenset({"JobComplete", "Failed", "Aborted"})

# Maximum number of retries for transient HTTP errors — matches salesforce_bulk.py.
_MAX_RETRIES = 3

# Locator sentinel returned by Salesforce when there are no more pages.
_LOCATOR_DONE = "null"

# ── Exceptions ─────────────────────────────────────────────────────────────────


class BulkQueryError(Exception):
    """Raised when the Salesforce Bulk Query API returns a non-retriable error.

    Analogous to ``BulkAPIError`` in :mod:`app.services.salesforce_bulk`.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class BulkQueryJobFailed(BulkQueryError):
    """Raised when Salesforce reports a terminal ``Failed`` or ``Aborted`` state.

    Callers (e.g. the orchestrator) can catch this specifically to mark the
    step as failed and surface a clear error message.  ``final_state`` carries
    the raw Salesforce state string (``"Failed"`` or ``"Aborted"``).
    """

    def __init__(
        self,
        message: str,
        *,
        final_state: str,
        status_code: Optional[int] = None,
        body: str = "",
        sf_job_response: Optional[dict] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, body=body)
        self.final_state = final_state
        self.sf_job_response = sf_job_response


# ── Result dataclass ────────────────────────────────────────────────────────────


@dataclass
class BulkQueryResult:
    """Typed result returned by :func:`run_bulk_query`.

    Attributes:
        row_count:    Number of data rows written (header row excluded).
        byte_count:   Total bytes written to the output sink.
        artefact_uri: Reference returned by ``OutputStorage.open_writer`` /
                      ``write_bytes`` — a relative path for local storage or
                      an ``s3://`` URI for S3.
        final_state:  Terminal Salesforce job state (``"JobComplete"``,
                      ``"Failed"``, or ``"Aborted"``).  Always
                      ``"JobComplete"`` on the happy path; ``"Failed"`` /
                      ``"Aborted"`` are surfaced via :exc:`BulkQueryJobFailed`
                      before a result is returned, so callers that catch that
                      exception can inspect ``exc.final_state`` directly.
    """

    row_count: int
    byte_count: int
    artefact_uri: str
    final_state: str
    sf_job_response: Optional[dict] = None


# ── HTTP helper ─────────────────────────────────────────────────────────────────

# ---- bulk query job ----


async def _request(
    client: httpx.AsyncClient,
    access_token: str,
    method: str,
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    **kwargs: Any,
) -> httpx.Response:
    """Authenticated HTTP request with retry logic.

    Matches the retry behaviour of ``SalesforceBulkClient._request``:
    - 3 retries with 1 s / 2 s / 4 s exponential backoff on 5xx and 429.
    - 429 honours ``Retry-After`` (integer seconds) when present.

    Args:
        client:       Active :class:`httpx.AsyncClient`.
        access_token: Salesforce access token (Bearer).
        method:       HTTP verb (``"GET"``, ``"POST"``).
        url:          Fully-qualified URL.
        headers:      Additional headers merged with the auth header.
        **kwargs:     Forwarded to :meth:`httpx.AsyncClient.request`.

    Returns:
        The :class:`httpx.Response` for the first non-retriable status code.

    Raises:
        BulkQueryError: After all retries are exhausted.
    """
    merged: dict[str, str] = {"Authorization": f"Bearer {access_token}"}
    if headers:
        merged.update(headers)

    last_response: Optional[httpx.Response] = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = await client.request(method, url, headers=merged, **kwargs)
        except httpx.RequestError as exc:
            if attempt == _MAX_RETRIES:
                raise BulkQueryError(
                    f"Network error on {method} {url} after {_MAX_RETRIES} retries: {exc}"
                ) from exc
            wait = float(2**attempt)
            logger.warning(
                "bulk_query: network error on %s %s (attempt %d/%d), retrying in %.0f s: %s",
                method, url, attempt + 1, _MAX_RETRIES + 1, wait, exc,
            )
            await asyncio.sleep(wait)
            continue

        last_response = response

        if response.status_code == 429:
            if attempt == _MAX_RETRIES:
                break
            raw = response.headers.get("Retry-After", "")
            wait = float(raw) if raw.isdigit() else float(2**attempt)
            logger.warning(
                "bulk_query: rate-limited (429) on %s %s (attempt %d/%d), "
                "retrying in %.1f s",
                method, url, attempt + 1, _MAX_RETRIES + 1, wait,
                extra={
                    "event_name": BulkQueryEvent.RATE_LIMITED,
                    "outcome_code": OutcomeCode.RATE_LIMITED,
                },
            )
            await asyncio.sleep(wait)
            continue

        if response.status_code >= 500:
            if attempt == _MAX_RETRIES:
                break
            wait = float(2**attempt)
            logger.warning(
                "bulk_query: server error %d on %s %s (attempt %d/%d), "
                "retrying in %.0f s",
                response.status_code, method, url, attempt + 1, _MAX_RETRIES + 1, wait,
                extra={
                    "event_name": BulkQueryEvent.REQUEST_RETRIED,
                    "outcome_code": None,
                },
            )
            await asyncio.sleep(wait)
            continue

        return response

    assert last_response is not None
    raise BulkQueryError(
        f"{method} {url} failed after {_MAX_RETRIES} retries: "
        f"HTTP {last_response.status_code}",
        status_code=last_response.status_code,
        body=last_response.text,
    )


# ── Core executor ───────────────────────────────────────────────────────────────


async def run_bulk_query(
    *,
    soql: str,
    operation: str,
    instance_url: str,
    access_token: str,
    output_storage: OutputStorage,
    relative_path: str,
    api_version: Optional[str] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> BulkQueryResult:
    """Execute a Salesforce Bulk API 2.0 query job and stream results to storage.

    Creates the query job, polls until it reaches a terminal state, then
    paginates the results endpoint — streaming each page directly into
    ``output_storage.open_writer(relative_path)`` without buffering the full
    result set in memory.

    The CSV header row is written exactly once (from the first page).
    Subsequent pages have their header row stripped before writing.

    Zero-row results produce a header-only file (the header from the empty
    first page is still written).

    Args:
        soql:           SOQL query string.
        operation:      ``"query"`` or ``"queryAll"``.
        instance_url:   Salesforce instance URL (e.g. ``"https://myorg.my.salesforce.com"``).
        access_token:   Salesforce Bearer access token.
        output_storage: :class:`~app.services.output_storage.OutputStorage`
                        instance to write results to.
        relative_path:  Target path relative to the storage root.
                        Recommended form: ``"<run_id>/<seq>-<object>-<timestamp>.csv"``.
        api_version:    Salesforce API version (e.g. ``"v62.0"``).  Defaults
                        to ``settings.sf_api_version``.
        http_client:    Optional pre-configured :class:`httpx.AsyncClient`.
                        When ``None`` (default) a new client is created and
                        owned by this function.

    Returns:
        :class:`BulkQueryResult` with ``row_count``, ``byte_count``,
        ``artefact_uri``, and ``final_state``.

    Raises:
        BulkQueryJobFailed: If Salesforce reports a terminal ``Failed`` or
            ``Aborted`` state.
        BulkQueryError: For HTTP-level failures that exhaust all retries.
    """
    version = api_version or settings.sf_api_version
    base = instance_url.rstrip("/")
    query_base = f"{base}/services/data/{version}/jobs/query"

    # Derive object_name from SOQL for metric labels (best-effort; fall back to
    # the operation string if parsing fails).  We extract the token after FROM.
    _from_match = __import__("re").search(r"(?i)\bFROM\s+(\w+)", soql)
    _object_name = _from_match.group(1) if _from_match else operation

    owns_client = http_client is None
    if owns_client:
        http_client = httpx.AsyncClient(timeout=30.0)

    try:
        with tracing.bulk_query_span(object_name=_object_name, operation=operation) as span:
            # ── 1. Create the query job ──────────────────────────────────────
            logger.debug(
                "bulk_query: SOQL=%s",
                soql,
                extra={
                    "event_name": BulkQueryEvent.JOB_CREATED,
                    "outcome_code": None,
                },
            )
            create_resp = await _request(
                http_client,
                access_token,
                "POST",
                query_base,
                headers={"Content-Type": "application/json"},
                json={"operation": operation, "query": soql},
            )
            if create_resp.status_code != 200:
                raise BulkQueryError(
                    f"create query job failed: HTTP {create_resp.status_code}",
                    status_code=create_resp.status_code,
                    body=create_resp.text,
                )
            job_id: str = create_resp.json()["id"]
            job_url = f"{query_base}/{job_id}"
            span.set_attribute("salesforce.job.id", job_id)
            logger.info(
                "bulk_query: created query job %s (operation=%s soql=%s)",
                job_id, operation, sanitize_soql(soql),
                extra={
                    "event_name": BulkQueryEvent.JOB_CREATED,
                    "outcome_code": None,
                    "sf_job_id": job_id,
                    "operation": operation,
                },
            )
            record_bulk_query_job_created(_object_name, operation)

            # ── 2. Poll until terminal ───────────────────────────────────────
            final_state, final_body = await _poll_query_job(http_client, access_token, job_url, job_id)

            if final_state != "JobComplete":
                logger.warning(
                    "bulk_query: job %s reached non-success terminal state %s",
                    job_id, final_state,
                    extra={
                        "event_name": BulkQueryEvent.JOB_FAILED,
                        "outcome_code": OutcomeCode.QUERY_SF_JOB_FAILED,
                        "sf_job_id": job_id,
                        "final_state": final_state,
                    },
                )
                record_bulk_query_job_failed(_object_name, operation)
                raise BulkQueryJobFailed(
                    f"Bulk query job {job_id} ended in state {final_state!r}",
                    final_state=final_state,
                    sf_job_response=final_body,
                )

            # ── 3 & 4. Paginate results and stream to storage ────────────────
            row_count, byte_count, page_count, artefact_uri = await _stream_results(
                http_client,
                access_token,
                job_url,
                output_storage,
                relative_path,
            )

            logger.info(
                "bulk_query: job %s complete — %d rows, %d bytes, %d page(s) → %s",
                job_id, row_count, byte_count, page_count, artefact_uri,
                extra={
                    "event_name": BulkQueryEvent.JOB_COMPLETED,
                    "outcome_code": OutcomeCode.OK,
                    "sf_job_id": job_id,
                    "row_count": row_count,
                    "byte_count": byte_count,
                    "page_count": page_count,
                    "artefact_uri": artefact_uri,
                },
            )
            record_bulk_query_job_completed(
                _object_name,
                operation,
                row_count=row_count,
                byte_count=byte_count,
                page_count=page_count,
            )

            return BulkQueryResult(
                row_count=row_count,
                byte_count=byte_count,
                artefact_uri=artefact_uri,
                final_state=final_state,
                sf_job_response=final_body,
            )

    finally:
        if owns_client:
            await http_client.aclose()


# ── Private helpers ─────────────────────────────────────────────────────────────


async def _poll_query_job(
    client: httpx.AsyncClient,
    access_token: str,
    job_url: str,
    job_id: str,
) -> tuple[str, dict]:
    """Poll a query job until it reaches a terminal state.

    Uses the same exponential-backoff strategy as ``SalesforceBulkClient.poll_job``:
    starts at ``sf_poll_interval_initial`` seconds, doubles each iteration up
    to ``sf_poll_interval_max``.  Respects ``sf_job_max_poll_seconds`` (0 = no cap).

    Args:
        client:       Active HTTP client.
        access_token: Salesforce Bearer token.
        job_url:      Fully-qualified URL for the query job status endpoint.
        job_id:       Salesforce job ID (for log messages).

    Returns:
        ``(final_state, final_body)`` — terminal state string
        (``"JobComplete"``, ``"Failed"``, or ``"Aborted"``) and the full
        Salesforce response body at the terminal poll, for observability.

    Raises:
        BulkQueryError: On HTTP failure or poll timeout.
    """
    interval = float(settings.sf_poll_interval_initial)
    max_interval = float(settings.sf_poll_interval_max)
    max_poll_seconds = int(settings.sf_job_max_poll_seconds)
    start = time.monotonic()

    while True:
        resp = await _request(client, access_token, "GET", job_url)
        if resp.status_code != 200:
            raise BulkQueryError(
                f"poll query job {job_id} failed: HTTP {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )

        body = resp.json()
        state: str = body.get("state", "")
        records_processed: int = body.get("numberRecordsProcessed", 0)

        logger.debug(
            "bulk_query: job %s state=%s processed=%d",
            job_id, state, records_processed,
            extra={
                "event_name": BulkQueryEvent.JOB_POLLED,
                "outcome_code": None,
                "sf_job_id": job_id,
                "state": state,
            },
        )

        if state in _QUERY_TERMINAL_STATES:
            logger.info(
                "bulk_query: job %s reached terminal state %s (processed=%d)",
                job_id, state, records_processed,
                extra={
                    "event_name": BulkQueryEvent.JOB_POLLED,
                    "outcome_code": OutcomeCode.OK if state == "JobComplete" else OutcomeCode.QUERY_SF_JOB_FAILED,
                    "sf_job_id": job_id,
                    "final_state": state,
                },
            )
            return state, body

        if max_poll_seconds > 0 and (time.monotonic() - start) >= max_poll_seconds:
            raise BulkQueryError(
                f"poll_query_job timed out for {job_id} after {max_poll_seconds}s "
                f"(last state={state})",
            )

        await asyncio.sleep(interval)
        interval = min(interval * 2.0, max_interval)


async def _fetch_results_page(
    client: httpx.AsyncClient,
    access_token: str,
    url: str,
    params: dict[str, str],
    page_number: int,
    is_first_page: bool,
    writer: Any,
) -> tuple[int, Optional[str]]:
    """Fetch one results page and stream it into *writer*.

    Strips the CSV header row on all pages except the first.
    Implements a simple retry loop (up to ``_MAX_RETRIES``) for 429 and 5xx
    responses — httpx's stream context does not compose with the generic
    ``_request`` helper since we need to stream the body.

    Args:
        client:       Active HTTP client.
        access_token: Salesforce Bearer token.
        url:          Results endpoint URL (with no locator — pass via *params*).
        params:       Query parameters (may include ``locator``).
        page_number:  0-based page index (used for logging).
        is_first_page: When ``True`` the header row is written; when ``False``
                       the header row is stripped.
        writer:       :class:`AsyncWritable` to stream bytes into.

    Returns:
        A 2-tuple ``(bytes_written, next_locator)`` where ``next_locator`` is
        ``None`` when there are no more pages.

    Raises:
        BulkQueryError: On persistent HTTP errors.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    bytes_written = 0

    for attempt in range(_MAX_RETRIES + 1):
        async with client.stream("GET", url, headers=headers, params=params) as resp:
            if resp.status_code == 429:
                if attempt == _MAX_RETRIES:
                    raise BulkQueryError(
                        f"results fetch page {page_number + 1} rate-limited after "
                        f"{_MAX_RETRIES} retries",
                        status_code=429,
                    )
                raw = resp.headers.get("Retry-After", "")
                wait = float(raw) if raw.isdigit() else float(2**attempt)
                logger.warning(
                    "bulk_query: rate-limited (429) on results page %d (attempt %d/%d), "
                    "retrying in %.1f s",
                    page_number + 1, attempt + 1, _MAX_RETRIES + 1, wait,
                    extra={
                        "event_name": BulkQueryEvent.RATE_LIMITED,
                        "outcome_code": OutcomeCode.RATE_LIMITED,
                    },
                )
                # Must consume the body before sleeping and retrying.
                await resp.aread()
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 500:
                if attempt == _MAX_RETRIES:
                    body_text = await resp.aread()
                    raise BulkQueryError(
                        f"results fetch page {page_number + 1} failed after "
                        f"{_MAX_RETRIES} retries: HTTP {resp.status_code}",
                        status_code=resp.status_code,
                        body=body_text.decode("utf-8", errors="replace"),
                    )
                wait = float(2**attempt)
                logger.warning(
                    "bulk_query: server error %d on results page %d (attempt %d/%d), "
                    "retrying in %.0f s",
                    resp.status_code, page_number + 1, attempt + 1, _MAX_RETRIES + 1, wait,
                    extra={
                        "event_name": BulkQueryEvent.REQUEST_RETRIED,
                        "outcome_code": None,
                    },
                )
                await resp.aread()
                await asyncio.sleep(wait)
                continue

            if resp.status_code != 200:
                body_text = await resp.aread()
                raise BulkQueryError(
                    f"results fetch failed (page {page_number + 1}): "
                    f"HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    body=body_text.decode("utf-8", errors="replace"),
                )

            # Success — read the locator from response headers.
            raw_locator: Optional[str] = resp.headers.get("Sforce-Locator")
            if raw_locator == _LOCATOR_DONE or raw_locator == "" or raw_locator is None:
                next_locator: Optional[str] = None
            else:
                next_locator = raw_locator

            if is_first_page:
                # Write all bytes verbatim (includes header row).
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        bytes_written += len(chunk)
                        await writer.write(chunk)
            else:
                # Strip the CSV header row (everything up to and including the
                # first newline character).  The header may span multiple chunks
                # so we buffer until we find the '\n'.
                header_stripped = False
                leftover = bytearray()

                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue

                    if header_stripped:
                        bytes_written += len(chunk)
                        await writer.write(chunk)
                    else:
                        leftover.extend(chunk)
                        nl_pos = leftover.find(b"\n")
                        if nl_pos != -1:
                            header_stripped = True
                            rest = bytes(leftover[nl_pos + 1:])
                            leftover = bytearray()
                            if rest:
                                bytes_written += len(rest)
                                await writer.write(rest)
                        # else: still accumulating the header — keep buffering.

                # Any remaining leftover is entirely header (no newline found);
                # discard it.

            logger.debug(
                "bulk_query: page %d downloaded — %d bytes, next_locator=%s",
                page_number + 1, bytes_written, next_locator,
                extra={
                    "event_name": BulkQueryEvent.JOB_PAGE_DOWNLOADED,
                    "outcome_code": None,
                    "page_index": page_number,
                },
            )
            return bytes_written, next_locator

    # Should be unreachable — all retry exhaustion paths raise above.
    raise BulkQueryError(f"results fetch page {page_number + 1}: retry loop exhausted")


async def _stream_results(
    client: httpx.AsyncClient,
    access_token: str,
    job_url: str,
    output_storage: OutputStorage,
    relative_path: str,
) -> tuple[int, int, int, str]:
    """Paginate the results endpoint and stream pages to ``output_storage``.

    The CSV header row (first line) is written exactly once — from page 1.
    All subsequent pages have their header row stripped before writing.

    Zero-row results produce a header-only file.

    Args:
        client:         Active HTTP client.
        access_token:   Salesforce Bearer token.
        job_url:        Base URL for the query job.
        output_storage: Destination storage.
        relative_path:  Target relative path within the storage.

    Returns:
        A 4-tuple ``(row_count, byte_count, page_count, artefact_uri)``.
        ``artefact_uri`` equals ``relative_path`` — the concrete storage
        reference for local storage.  S3 callers should wrap the result.
        TODO(SFBL-176): resolve artefact_uri for S3 in per-step destination
        resolution ticket.
    """
    results_url = f"{job_url}/results"
    page_number = 0
    byte_count = 0
    locator: Optional[str] = None

    async with output_storage.open_writer(relative_path) as writer:
        while True:
            params: dict[str, str] = {}
            if locator is not None:
                params["locator"] = locator

            page_bytes, next_locator = await _fetch_results_page(
                client,
                access_token,
                results_url,
                params,
                page_number,
                is_first_page=(page_number == 0),
                writer=writer,
            )
            byte_count += page_bytes
            page_number += 1
            locator = next_locator
            if locator is None:
                break

    artefact_uri = output_storage.resolve_uri(relative_path)

    # Derive row_count by reading back the committed file via the
    # provider-specific URI (local relative path or s3://bucket/key).
    # TODO(SFBL-171): replace with inline newline counting during streaming to
    # avoid this extra I/O.
    row_count = 0
    try:
        data = output_storage.read_bytes(artefact_uri)
        # Count non-empty lines, then subtract 1 for the header row.
        all_lines = [ln for ln in data.split(b"\n") if ln]
        row_count = max(0, len(all_lines) - 1)
    except Exception as exc:  # noqa: BLE001
        # Read-back is best-effort: if the provider cannot return the bytes
        # (e.g. S3 download permissions), log and fall through with 0 so the
        # caller can still record the artefact URI.  SFBL-171 will replace
        # this path with inline counting.
        logger.warning(
            "Could not read back %s to count rows: %s", artefact_uri, exc,
        )
        row_count = 0

    return row_count, byte_count, page_number, artefact_uri
