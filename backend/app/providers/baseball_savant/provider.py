"""Statcast provider. One request per calendar date range, not per game."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

from app.core.logging import get_logger
from app.providers.base import DataCategory, ProviderResult, ProviderStatus
from app.providers.baseball_savant.client import BaseballSavantClient

log = get_logger(__name__)

SOURCE_NAME = "baseball_savant"


class BaseballSavantProvider:
    """Implements the statcast category of the provider contract."""

    name = SOURCE_NAME
    category = DataCategory.STATCAST

    def __init__(self, client: BaseballSavantClient | None = None) -> None:
        self._client = client or BaseballSavantClient()

    def close(self) -> None:
        self._client.close()

    @property
    def request_count(self) -> int:
        return self._client.request_count

    def fetch_statcast_range(
        self, start: date, end: date, season: int | None = None
    ) -> ProviderResult[pd.DataFrame]:
        """Every regular-season pitch thrown between two dates, inclusive.

        The raw payload stored alongside is the *shape* of the response — row
        count, columns and the exact request — rather than megabytes of CSV
        re-encoded as JSON. Storing the full export would multiply an already
        storage-bound table by its own size again for no audit gain: the export
        is reproducible from the request, and the request is what is kept.
        """
        params: dict[str, Any] = {
            "all": "true",
            "hfGT": "R|",  # regular season only
            "hfSea": f"{season}|" if season else "",
            "player_type": "pitcher",
            "game_date_gt": start.isoformat(),
            "game_date_lt": end.isoformat(),
            "min_pitches": 0,
            "type": "details",
        }
        retrieved_at = datetime.now(UTC)
        try:
            frame = self._client.get_csv(params)
        except Exception as exc:  # noqa: BLE001 - provider boundary
            log.error("statcast.failed", start=str(start), end=str(end), error=str(exc))
            return ProviderResult.unavailable(
                SOURCE_NAME,
                DataCategory.STATCAST,
                f"Statcast request for {start}..{end} failed: {exc}",
                endpoint="/statcast_search/csv",
                request_params=params,
            )

        return ProviderResult(
            status=ProviderStatus.OK if not frame.empty else ProviderStatus.PARTIAL,
            source_name=SOURCE_NAME,
            category=DataCategory.STATCAST,
            retrieved_at=retrieved_at,
            # Per-game knowledge times are attached during normalization. This
            # one is the retrieval instant and is never used as an as-of cut.
            knowledge_time=retrieved_at,
            data=frame,
            raw_payload={
                "rows": int(len(frame)),
                "columns": list(frame.columns)[:200],
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            message=None if not frame.empty else f"No Statcast rows for {start}..{end}",
            endpoint="/statcast_search/csv",
            request_params=params,
        )


__all__ = ["SOURCE_NAME", "BaseballSavantProvider"]
