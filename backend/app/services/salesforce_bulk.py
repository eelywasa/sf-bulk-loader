"""Salesforce Bulk API 2.0 client (spec §4.2).

Full job lifecycle::

    create_job → upload_csv → close_job → poll_job → get_*_results

HTTP retry policy:
    All requests retry up to 3 times with exponential backoff (1 s, 2 s, 4 s)
    on 5xx and 429 responses. 429 additionally honours the ``Retry-After`` header.

Polling backoff:
    ``poll_job`` starts at ``sf_poll_interval_initial`` seconds (default 5 s) and
    doubles each poll up to ``sf_poll_interval_max`` seconds (default 30 s).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Salesforce Bulk API 2.0 terminal job states.
_TERMINAL_STATES: frozenset[str] = frozenset({"JobComplete", "Failed", "Aborted"})

# Maximum number of retries for transient HTTP errors (5xx, 429, network errors).
_MAX_RETRIES = 3


# ── Exception ───────────────────────────────────────────────────────────────────


class BulkAPIError(Exception):
    """Raised when a Salesforce Bulk API call fails unrecoverably."""

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


# ── Client ───────────────────────────────────────────────────────────────────────


class SalesforceBulkClient:
    """Async Salesforce Bulk API 2.0 client.

    Recommended usage (context manager — owns its own :class:`httpx.AsyncClient`)::

        async with SalesforceBulkClient(instance_url, access_token) as client:
            job_id = await client.create_job("Account", "insert")
            await client.upload_csv(job_id, csv_bytes)
            await client.close_job(job_id)
            state = await client.poll_job(job_id)
            success_csv = await client.get_success_results(job_id)

    Injected-client usage (useful for testing or sharing a connection pool)::

        async with httpx.AsyncClient() as http:
            client = SalesforceBulkClient(instance_url, access_token, http_client=http)
            job_id = await client.create_job(...)
    """

    def __init__(
        self,
        instance_url: str,
        access_token: str,
        api_version: Optional[str] = None,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._base_url = instance_url.rstrip("/")
        self._access_token = access_token
        self._api_version = api_version or settings.sf_api_version
        # If no client supplied we create (and own) one in __aenter__.
        self._client: Optional[httpx.AsyncClient] = http_client
        self._owns_client: bool = http_client is None

    # ── Context manager ─────────────────────────────────────────────────────────

    async def __aenter__(self) -> SalesforceBulkClient:
        if self._owns_client:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── URL helpers ─────────────────────────────────────────────────────────────

    @property
    def _ingest_base(self) -> str:
        return f"{self._base_url}/services/data/{self._api_version}/jobs/ingest"

    def _job_url(self, sf_job_id: str) -> str:
        return f"{self._ingest_base}/{sf_job_id}"

    # ── HTTP helper with retry ───────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an authenticated HTTP request with retry logic.

        The ``Authorization: Bearer`` header is always injected.  Additional
        headers (e.g. ``Content-Type``) can be supplied via *headers*.

        Retries up to ``_MAX_RETRIES`` times with exponential backoff
        (1 s → 2 s → 4 s) on 5xx server errors and 429 rate-limit responses.
        For 429 responses the ``Retry-After`` header (integer seconds) takes
        precedence over the computed backoff delay.

        Args:
            method: HTTP verb (``"GET"``, ``"POST"``, ``"PUT"``, ``"PATCH"``).
            url: Fully-qualified request URL.
            headers: Extra request headers merged with the auth header.
            **kwargs: Forwarded to :meth:`httpx.AsyncClient.request` —
                e.g. ``json=``, ``content=``, ``data=``.

        Returns:
            The :class:`httpx.Response` for the first non-retriable status.

        Raises:
            BulkAPIError: After all retries are exhausted or on a persistent
                network-level error.
            RuntimeError: If called without an active HTTP client.
        """
        if self._client is None:
            raise RuntimeError(
                "SalesforceBulkClient has no HTTP client. "
                "Use it as an async context manager or pass http_client=."
            )

        merged_headers: dict[str, str] = {
            "Authorization": f"Bearer {self._access_token}",
        }
        if headers:
            merged_headers.update(headers)

        last_response: Optional[httpx.Response] = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    headers=merged_headers,
                    **kwargs,
                )
            except httpx.RequestError as exc:
                if attempt == _MAX_RETRIES:
                    raise BulkAPIError(
                        f"Network error on {method} {url} after {_MAX_RETRIES} "
                        f"retries: {exc}"
                    ) from exc
                wait = float(2**attempt)
                logger.warning(
                    "Network error on %s %s (attempt %d/%d), retrying in %.0f s: %s",
                    method,
                    url,
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    wait,
                    exc,
                )
                await asyncio.sleep(wait)
                continue

            last_response = response

            if response.status_code == 429:
                if attempt == _MAX_RETRIES:
                    break  # Fall through to raise below.
                retry_after_raw = response.headers.get("Retry-After", "")
                wait = (
                    float(retry_after_raw)
                    if retry_after_raw.isdigit()
                    else float(2**attempt)
                )
                logger.warning(
                    "Rate-limited (429) on %s %s (attempt %d/%d), "
                    "retrying in %.1f s",
                    method,
                    url,
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    wait,
                )
                await asyncio.sleep(wait)
                continue

            if response.status_code >= 500:
                if attempt == _MAX_RETRIES:
                    break  # Fall through to raise below.
                wait = float(2**attempt)
                logger.warning(
                    "Server error %d on %s %s (attempt %d/%d), "
                    "retrying in %.0f s",
                    response.status_code,
                    method,
                    url,
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    wait,
                )
                await asyncio.sleep(wait)
                continue

            # 1xx / 2xx / 3xx / 4xx — not retriable; return as-is.
            return response

        # All retries exhausted.
        assert last_response is not None  # Always set when status_code triggered.
        raise BulkAPIError(
            f"{method} {url} failed after {_MAX_RETRIES} retries: "
            f"HTTP {last_response.status_code}",
            status_code=last_response.status_code,
            body=last_response.text,
        )

    # ── Public API ───────────────────────────────────────────────────────────────

    async def create_job(
        self,
        object_name: str,
        operation: str,
        *,
        external_id_field: Optional[str] = None,
        assignment_rule_id: Optional[str] = None,
    ) -> str:
        """Create a Bulk API 2.0 ingest job and return its Salesforce job ID.

        Args:
            object_name: Salesforce object API name (e.g. ``"Account"``).
            operation: One of ``"insert"``, ``"update"``, ``"upsert"``, ``"delete"``.
            external_id_field: Required when *operation* is ``"upsert"``.
            assignment_rule_id: Optional Salesforce assignment rule ID.

        Returns:
            The Salesforce job ID string (``sf_job_id``).

        Raises:
            BulkAPIError: If job creation fails.
        """
        payload: dict[str, str] = {
            "object": object_name,
            "operation": operation,
            "contentType": "CSV",
            "lineEnding": "LF",
        }
        if external_id_field:
            payload["externalIdFieldName"] = external_id_field
        if assignment_rule_id:
            payload["assignmentRuleId"] = assignment_rule_id

        response = await self._request(
            "POST",
            self._ingest_base,
            headers={"Content-Type": "application/json"},
            json=payload,
        )

        if response.status_code != 200:
            raise BulkAPIError(
                f"create_job failed for {object_name}/{operation}: "
                f"HTTP {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )

        body = response.json()
        sf_job_id: str = body["id"]
        logger.info(
            "Created Bulk API 2.0 job %s for %s %s", sf_job_id, operation, object_name
        )
        return sf_job_id

    async def upload_csv(self, sf_job_id: str, csv_content: bytes) -> None:
        """Upload CSV data to an open ingest job.

        Args:
            sf_job_id: Salesforce job ID returned by :meth:`create_job`.
            csv_content: UTF-8-encoded CSV bytes with LF line endings.

        Raises:
            BulkAPIError: If the upload fails.
        """
        url = f"{self._job_url(sf_job_id)}/batches"
        response = await self._request(
            "PUT",
            url,
            headers={"Content-Type": "text/csv"},
            content=csv_content,
        )

        if response.status_code not in (200, 201, 204):
            raise BulkAPIError(
                f"upload_csv failed for job {sf_job_id}: HTTP {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )

        logger.info(
            "Uploaded %d bytes of CSV data to job %s", len(csv_content), sf_job_id
        )

    async def close_job(self, sf_job_id: str) -> None:
        """Signal that all CSV data has been uploaded; begin Salesforce processing.

        Transitions the job to ``UploadComplete`` state.

        Raises:
            BulkAPIError: If the state transition fails.
        """
        response = await self._request(
            "PATCH",
            self._job_url(sf_job_id),
            headers={"Content-Type": "application/json"},
            json={"state": "UploadComplete"},
        )

        if response.status_code != 200:
            raise BulkAPIError(
                f"close_job failed for job {sf_job_id}: HTTP {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )

        logger.info("Closed job %s (UploadComplete)", sf_job_id)

    async def poll_job(self, sf_job_id: str) -> str:
        """Poll a job until it reaches a terminal state.

        Makes an initial status request immediately, then sleeps between
        subsequent polls with exponential backoff starting at
        ``sf_poll_interval_initial`` seconds (default 5 s) and doubling each
        poll up to ``sf_poll_interval_max`` seconds (default 30 s).

        For 429 responses during polling the ``Retry-After`` header is honoured
        by the underlying :meth:`_request` retry logic.

        Args:
            sf_job_id: The Salesforce job ID.

        Returns:
            The terminal Salesforce state string:
            ``"JobComplete"``, ``"Failed"``, or ``"Aborted"``.

        Raises:
            BulkAPIError: If a status request fails after retries.
        """
        url = self._job_url(sf_job_id)
        interval = float(settings.sf_poll_interval_initial)
        max_interval = float(settings.sf_poll_interval_max)

        while True:
            response = await self._request("GET", url)

            if response.status_code != 200:
                raise BulkAPIError(
                    f"poll_job failed for {sf_job_id}: HTTP {response.status_code}",
                    status_code=response.status_code,
                    body=response.text,
                )

            body = response.json()
            state: str = body.get("state", "")
            records_processed: int = body.get("numberRecordsProcessed", 0)
            records_failed: int = body.get("numberRecordsFailed", 0)

            logger.debug(
                "Job %s state=%s processed=%d failed=%d",
                sf_job_id,
                state,
                records_processed,
                records_failed,
            )

            if state in _TERMINAL_STATES:
                logger.info(
                    "Job %s reached terminal state %s (processed=%d, failed=%d)",
                    sf_job_id,
                    state,
                    records_processed,
                    records_failed,
                )
                return state

            await asyncio.sleep(interval)
            interval = min(interval * 2.0, max_interval)

    async def get_success_results(self, sf_job_id: str) -> bytes:
        """Download the successful records CSV for a completed job.

        Returns:
            Raw CSV bytes of successfully processed records.

        Raises:
            BulkAPIError: If the download fails.
        """
        url = f"{self._job_url(sf_job_id)}/successfulResults"
        return await self._fetch_results(sf_job_id, url, "successfulResults")

    async def get_failed_results(self, sf_job_id: str) -> bytes:
        """Download the failed records CSV for a completed job.

        Returns:
            Raw CSV bytes of records that Salesforce rejected.

        Raises:
            BulkAPIError: If the download fails.
        """
        url = f"{self._job_url(sf_job_id)}/failedResults"
        return await self._fetch_results(sf_job_id, url, "failedResults")

    async def get_unprocessed_results(self, sf_job_id: str) -> bytes:
        """Download the unprocessed records CSV for a completed or aborted job.

        Returns:
            Raw CSV bytes of records that were not processed.

        Raises:
            BulkAPIError: If the download fails.
        """
        url = f"{self._job_url(sf_job_id)}/unprocessedrecords"
        return await self._fetch_results(sf_job_id, url, "unprocessedrecords")

    async def abort_job(self, sf_job_id: str) -> None:
        """Abort an open or in-progress Salesforce job.

        Raises:
            BulkAPIError: If the abort request fails.
        """
        response = await self._request(
            "PATCH",
            self._job_url(sf_job_id),
            headers={"Content-Type": "application/json"},
            json={"state": "Aborted"},
        )

        if response.status_code != 200:
            raise BulkAPIError(
                f"abort_job failed for job {sf_job_id}: HTTP {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )

        logger.info("Aborted job %s", sf_job_id)

    # ── Private helpers ─────────────────────────────────────────────────────────

    async def _fetch_results(
        self, sf_job_id: str, url: str, result_type: str
    ) -> bytes:
        """Shared download logic for all three results endpoints."""
        response = await self._request("GET", url)

        if response.status_code != 200:
            raise BulkAPIError(
                f"get_{result_type} failed for job {sf_job_id}: "
                f"HTTP {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )

        logger.info(
            "Downloaded %s for job %s (%d bytes)",
            result_type,
            sf_job_id,
            len(response.content),
        )
        return response.content
