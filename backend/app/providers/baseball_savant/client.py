"""Rate-limited HTTP client for Baseball Savant's Statcast CSV export.

Savant is a smaller service than the MLB Stats API and a single request returns
megabytes, so the default spacing here is an order of magnitude slower. A
backfill is not urgent; being a good citizen is.

The two refusals below are the same class of protection the MLB client applies
to `/stats`, and exist for the same reason.
"""

from __future__ import annotations

import io
import time
from typing import Any

import httpx
import pandas as pd

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

BASE_URL = "https://baseballsavant.mlb.com"

# The only path this client will request. Savant's leaderboard endpoints return
# *current-season totals* per player; attaching one of those to a game played in
# April would embed that game's own result and every game since, exactly as the
# MLB /stats endpoints do. Aggregation happens in our feature layer, from dated
# rows (LEAKAGE_PREVENTION.md §14).
ALLOWED_PATH = "/statcast_search/csv"

# A request without both bounds is a request for everything Savant knows, which
# is the same leak wearing different clothes.
REQUIRED_PARAMS = ("game_date_gt", "game_date_lt")


class StatcastError(RuntimeError):
    """A Savant request failed after exhausting retries."""


class BaseballSavantClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout_s: float | None = None,
        min_interval_ms: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.base_url = (base_url or settings.statcast_base_url).rstrip("/")
        self.timeout_s = timeout_s or settings.statcast_timeout_s
        self.min_interval_s = (
            min_interval_ms if min_interval_ms is not None else settings.statcast_min_interval_ms
        ) / 1000.0
        self.max_retries = (
            max_retries if max_retries is not None else settings.statcast_max_retries
        )
        self._last_request_at = 0.0
        self._requests = 0
        self._client = httpx.Client(
            timeout=self.timeout_s,
            follow_redirects=True,
            headers={"User-Agent": f"{settings.app_name} (statcast ingest)"},
        )

    @property
    def request_count(self) -> int:
        return self._requests

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _guard(path: str, params: dict[str, Any]) -> None:
        if path.rstrip("/") != ALLOWED_PATH:
            raise ValueError(
                f"Refusing to request {path!r}. This client only issues "
                f"{ALLOWED_PATH!r}. Savant's leaderboard endpoints return "
                f"current-season aggregates, which would embed a game's own "
                f"result into its own inputs (LEAKAGE_PREVENTION.md §14)."
            )
        missing = [key for key in REQUIRED_PARAMS if not params.get(key)]
        if missing:
            raise ValueError(
                f"Refusing an unbounded Statcast request: {missing} not set. "
                f"Every request must name the date range it covers so the rows "
                f"it returns can be attributed to a knowledge time."
            )

    def get_csv(self, params: dict[str, Any], path: str = ALLOWED_PATH) -> pd.DataFrame:
        """Fetch one Statcast search as a DataFrame. Empty is a valid answer."""
        self._guard(path, params)
        url = f"{self.base_url}{path}"
        last: Exception | None = None

        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                response = self._client.get(url, params=params)
                self._requests += 1
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                text = response.text
                # Savant answers an empty search with a header row, or
                # occasionally with a bare error string.
                if not text.strip() or text.lstrip().startswith("<"):
                    return pd.DataFrame()
                frame = pd.read_csv(io.StringIO(text), low_memory=False)
                log.info(
                    "statcast.fetched",
                    rows=len(frame),
                    bytes=len(text),
                    start=params.get("game_date_gt"),
                    end=params.get("game_date_lt"),
                )
                return frame
            except Exception as exc:  # noqa: BLE001 - retried, then re-raised
                last = exc
                if attempt < self.max_retries:
                    backoff = 2.0 * (2**attempt)
                    log.warning(
                        "statcast.retry", attempt=attempt + 1, backoff_s=backoff, error=str(exc)
                    )
                    time.sleep(backoff)

        raise StatcastError(f"Statcast request failed after {self.max_retries} retries: {last}")


__all__ = ["ALLOWED_PATH", "BaseballSavantClient", "StatcastError"]
