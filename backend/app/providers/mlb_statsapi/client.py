"""Rate-limited, retrying HTTP client for the MLB Stats API."""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

SOURCE_NAME = "mlb_statsapi"

# Endpoints that return CURRENT season aggregates. Consuming these to build a
# feature for a past game is the leakage vector described in
# LEAKAGE_PREVENTION.md §3. The client refuses to call them.
FORBIDDEN_PATH_FRAGMENTS = ("/stats", "stats?stats=season", "/teams/stats")


class MlbStatsApiClient:
    """Thin transport layer. Parsing lives in the per-endpoint modules."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_s: float | None = None,
        min_interval_ms: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.base_url = (base_url or settings.mlb_statsapi_base_url).rstrip("/")
        self.timeout_s = timeout_s if timeout_s is not None else settings.mlb_statsapi_timeout_s
        self.min_interval_s = (
            (min_interval_ms if min_interval_ms is not None else settings.mlb_statsapi_min_interval_ms)
            / 1000.0
        )
        self.max_retries = (
            max_retries if max_retries is not None else settings.mlb_statsapi_max_retries
        )
        self._lock = threading.Lock()
        self._last_request_at = 0.0
        self._client = httpx.Client(
            timeout=self.timeout_s,
            headers={"User-Agent": "JerryMLBPredictionLab/1.0 (analytics)"},
            follow_redirects=True,
        )
        self.request_count = 0

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MlbStatsApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transport ---------------------------------------------------------
    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.min_interval_s:
                time.sleep(self.min_interval_s - elapsed)
            self._last_request_at = time.monotonic()

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Issue a GET. Raises httpx errors; callers convert to ProviderResult."""
        normalized = path if path.startswith("/") else f"/{path}"
        for fragment in FORBIDDEN_PATH_FRAGMENTS:
            if fragment in normalized:
                raise ValueError(
                    f"Refusing to call season-aggregate endpoint {normalized!r}. "
                    "Rolling statistics must be rebuilt from dated game logs "
                    "(see LEAKAGE_PREVENTION.md §3)."
                )

        url = f"{self.base_url}{normalized}"
        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                self.request_count += 1
                response = self._client.get(url, params=params)
                if response.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                backoff = min(2.0**attempt, 16.0)
                log.warning(
                    "mlb_statsapi.retry",
                    url=url,
                    attempt=attempt + 1,
                    backoff_s=backoff,
                    error=str(exc),
                )
                time.sleep(backoff)

        assert last_exc is not None
        raise last_exc
