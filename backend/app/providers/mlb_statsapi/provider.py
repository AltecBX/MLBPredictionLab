"""MLB Stats API provider implementations.

Implements ReferenceProvider, ScheduleProvider and ResultsProvider. Failures
are converted to ``ProviderResult(status=UNAVAILABLE)`` — nothing raises past
this boundary.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from app.core.logging import get_logger
from app.providers.base import (
    DataCategory,
    ProviderResult,
    ProviderStatus,
    RawBoxscore,
    RawGame,
    RawPlayer,
    RawTeam,
    RawVenue,
)
from app.providers.mlb_statsapi.client import SOURCE_NAME, MlbStatsApiClient
from app.providers.mlb_statsapi import mappers

log = get_logger(__name__)

SCHEDULE_HYDRATE = "probablePitcher,linescore,team,venue,weather,decisions,seriesStatus"


class MlbStatsApiProvider:
    """Single class implementing the three Phase 1 provider Protocols."""

    name = SOURCE_NAME

    def __init__(self, client: MlbStatsApiClient | None = None) -> None:
        self._client = client or MlbStatsApiClient()

    def close(self) -> None:
        self._client.close()

    @property
    def request_count(self) -> int:
        return self._client.request_count

    # -- helpers -----------------------------------------------------------
    def _result(
        self,
        category: DataCategory,
        data: Any,
        payload: dict[str, Any],
        endpoint: str,
        params: dict[str, Any],
        knowledge_time: datetime | None = None,
        status: ProviderStatus = ProviderStatus.OK,
        message: str | None = None,
    ) -> ProviderResult[Any]:
        now = datetime.now(UTC)
        return ProviderResult(
            status=status,
            source_name=self.name,
            category=category,
            retrieved_at=now,
            knowledge_time=knowledge_time or now,
            data=data,
            raw_payload=payload,
            endpoint=endpoint,
            request_params=params,
            message=message,
        )

    def _fail(
        self, category: DataCategory, endpoint: str, params: dict[str, Any], exc: Exception
    ) -> ProviderResult[Any]:
        log.error("provider.failed", source=self.name, endpoint=endpoint, error=str(exc))
        return ProviderResult.unavailable(
            self.name,
            category,
            f"MLB Stats API request to {endpoint} failed: {exc}",
            endpoint=endpoint,
            request_params=params,
        )

    # -- ReferenceProvider -------------------------------------------------
    def fetch_teams(self, season: int) -> ProviderResult[list[RawTeam]]:
        endpoint, params = "/teams", {"sportId": 1, "season": season}
        try:
            payload = self._client.get(endpoint, params)
        except Exception as exc:
            return self._fail(DataCategory.REFERENCE, endpoint, params, exc)
        teams = [mappers.map_team(node) for node in payload.get("teams", []) if node.get("id")]
        return self._result(DataCategory.REFERENCE, teams, payload, endpoint, params)

    def fetch_venues(self, season: int) -> ProviderResult[list[RawVenue]]:
        endpoint = "/venues"
        params = {"hydrate": "location,fieldInfo", "season": season}
        try:
            payload = self._client.get(endpoint, params)
        except Exception as exc:
            return self._fail(DataCategory.REFERENCE, endpoint, params, exc)
        venues = [mappers.map_venue(node) for node in payload.get("venues", []) if node.get("id")]
        return self._result(DataCategory.REFERENCE, venues, payload, endpoint, params)

    def fetch_people(self, player_ids: list[int]) -> ProviderResult[list[RawPlayer]]:
        if not player_ids:
            return self._result(DataCategory.REFERENCE, [], {}, "/people", {})
        endpoint = "/people"
        collected: list[RawPlayer] = []
        merged: dict[str, Any] = {"people": []}
        chunk_size = 100
        for i in range(0, len(player_ids), chunk_size):
            chunk = player_ids[i : i + chunk_size]
            params = {"personIds": ",".join(str(p) for p in chunk)}
            try:
                payload = self._client.get(endpoint, params)
            except Exception as exc:
                return self._fail(DataCategory.REFERENCE, endpoint, params, exc)
            nodes = payload.get("people", [])
            merged["people"].extend(nodes)
            collected.extend(mappers.map_player(n) for n in nodes if n.get("id"))
        status = (
            ProviderStatus.OK
            if len(collected) == len(set(player_ids))
            else ProviderStatus.PARTIAL
        )
        message = (
            None
            if status is ProviderStatus.OK
            else f"Resolved {len(collected)} of {len(set(player_ids))} requested players."
        )
        return self._result(
            DataCategory.REFERENCE,
            collected,
            merged,
            endpoint,
            {"count": len(player_ids)},
            status=status,
            message=message,
        )

    # -- ScheduleProvider --------------------------------------------------
    def fetch_schedule(self, start: date, end: date) -> ProviderResult[list[RawGame]]:
        endpoint = "/schedule"
        params = {
            "sportId": 1,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "hydrate": SCHEDULE_HYDRATE,
        }
        try:
            payload = self._client.get(endpoint, params)
        except Exception as exc:
            return self._fail(DataCategory.SCHEDULE, endpoint, params, exc)

        games: list[RawGame] = []
        skipped = 0
        for day in payload.get("dates", []):
            for node in day.get("games", []):
                game = mappers.map_game(node)
                if game is None:
                    skipped += 1
                    continue
                games.append(game)

        status = ProviderStatus.PARTIAL if skipped else ProviderStatus.OK
        message = f"{skipped} schedule entries could not be parsed." if skipped else None
        return self._result(
            DataCategory.SCHEDULE, games, payload, endpoint, params,
            status=status, message=message,
        )

    def fetch_probable_pitcher_ids(self, start: date, end: date) -> list[int]:
        """Player ids referenced as probable starters in a schedule window."""
        result = self.fetch_schedule(start, end)
        if not result.ok or result.data is None:
            return []
        ids: set[int] = set()
        for game in result.data:
            for pid in (game.home_probable_pitcher_id, game.away_probable_pitcher_id):
                if pid:
                    ids.add(pid)
        return sorted(ids)

    # -- ResultsProvider ---------------------------------------------------
    def fetch_boxscore(self, game_id: int) -> ProviderResult[RawBoxscore]:
        endpoint = f"/game/{game_id}/boxscore"
        params: dict[str, Any] = {}
        try:
            payload = self._client.get(endpoint, params)
        except Exception as exc:
            return self._fail(DataCategory.RESULTS, endpoint, params, exc)
        box = mappers.map_boxscore(game_id, payload)
        if not box.team_lines:
            return ProviderResult.unavailable(
                self.name,
                DataCategory.RESULTS,
                f"Boxscore for game {game_id} contained no team lines.",
                endpoint=endpoint,
            )
        return self._result(DataCategory.RESULTS, box, payload, endpoint, params)
