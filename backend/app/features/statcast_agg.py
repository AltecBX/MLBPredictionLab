"""Per-game Statcast aggregates, keyed by pitcher.

Two million pitches will not go into a pandas frame that a walk-forward
backtest slices thousands of times. They do not need to: every rolling window
this system asks for is a sum over whole games, so the pitch table is collapsed
once, in SQL, to **one row per game per pitcher** — the same shape as the
box-score `pitcher_games` frame that already exists, and small enough (~65,000
rows for three seasons) to slice in memory.

`knowledge_time` survives the collapse unchanged: it is the game's, every pitch
in a game shares it, and the as-of cut is applied to the aggregate exactly as it
is to any other fact.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger

log = get_logger(__name__)

# Savant `events` values, grouped. Written here rather than derived so that a
# rulebook change is a visible edit to a list.
STRIKEOUT_EVENTS = ("strikeout", "strikeout_double_play", "strikeout_triple_play")
WALK_EVENTS = ("walk", "intent_walk")

# One row per (game, pitcher). Counts filter on `is_pitch`, so the balls and
# strikes Savant records as awarded without a pitch never reach a denominator.
#
# The batted-ball join is on the pitch's own key, so it is one-to-at-most-one
# and cannot multiply rows. `is_starter` comes from the box score already
# ingested rather than from guessing at the first pitch of an inning.
PITCHER_GAME_SQL = text(
    """
    SELECT
      p.game_id,
      p.pitcher_id                                              AS player_id,
      g.game_date_utc,
      g.season,
      MAX(p.knowledge_time)                                     AS knowledge_time,
      COALESCE(BOOL_OR(s.is_starter), FALSE)                    AS is_starter,

      COUNT(*) FILTER (WHERE p.is_pitch)                        AS pitches,
      COUNT(*) FILTER (WHERE p.is_pitch AND p.is_swing)         AS swings,
      COUNT(*) FILTER (WHERE p.is_pitch AND p.is_whiff)         AS whiffs,
      COUNT(*) FILTER (WHERE p.is_pitch AND p.is_called_strike)  AS called_strikes,
      COUNT(*) FILTER (WHERE p.is_pitch AND NOT p.is_in_zone)   AS out_of_zone,
      COUNT(*) FILTER (
        WHERE p.is_pitch AND NOT p.is_in_zone AND p.is_swing
      )                                                          AS chases,

      COUNT(*) FILTER (WHERE p.pa_event IS NOT NULL)            AS plate_appearances,
      COUNT(*) FILTER (WHERE p.pa_event = ANY(:k_events))       AS strikeouts,
      COUNT(*) FILTER (WHERE p.pa_event = ANY(:bb_events))      AS walks,
      SUM(p.woba_denom)                                         AS woba_denom,

      -- xwOBA against, Savant's construction: the expected value of the
      -- contact where there was contact, and the actual value everywhere else
      -- — a walk is worth a walk, a strikeout is worth nothing. Restricting it
      -- to balls in play would score a pitcher on contact quality alone and
      -- credit him nothing for missing bats.
      SUM(COALESCE(b.estimated_woba, p.woba_value))
        FILTER (WHERE p.pa_event IS NOT NULL)                   AS xwoba_num,
      SUM(p.woba_value) FILTER (WHERE p.pa_event IS NOT NULL)   AS woba_num,

      -- Four-seam velocity is the standard reference point; mixing pitch types
      -- would make a change in usage look like a change in stuff.
      COUNT(p.release_speed) FILTER (WHERE p.pitch_type = 'FF') AS ff_count,
      SUM(p.release_speed)   FILTER (WHERE p.pitch_type = 'FF') AS ff_speed_sum,
      COUNT(p.release_extension) FILTER (WHERE p.is_pitch)      AS extension_count,
      SUM(p.release_extension)   FILTER (WHERE p.is_pitch)      AS extension_sum,

      COUNT(b.id)                                               AS balls_in_play,
      COUNT(*) FILTER (WHERE b.is_barrel)                       AS barrels,
      COUNT(*) FILTER (WHERE b.is_hard_hit)                     AS hard_hit,
      COUNT(b.launch_speed)                                     AS ev_count,
      SUM(b.launch_speed)                                       AS ev_sum
    FROM pitches p
    JOIN games g ON g.id = p.game_id
    LEFT JOIN batted_ball_events b
      ON  b.game_id      = p.game_id
      AND b.at_bat_index = p.at_bat_index
      AND b.pitch_number = p.pitch_number
    LEFT JOIN player_game_stats s
      ON  s.game_id   = p.game_id
      AND s.player_id = p.pitcher_id
      AND s.role      = 'pitcher'
    WHERE g.game_type = 'R'
    GROUP BY p.game_id, p.pitcher_id, g.game_date_utc, g.season
    """
)

# Columns that are counts and may legitimately be summed across a window.
COUNT_COLUMNS = (
    "pitches", "swings", "whiffs", "called_strikes", "out_of_zone", "chases",
    "plate_appearances", "strikeouts", "walks", "woba_denom",
    "xwoba_num", "woba_num", "ff_count", "ff_speed_sum",
    "extension_count", "extension_sum",
    "balls_in_play", "barrels", "hard_hit", "ev_count", "ev_sum",
)


def load_pitcher_statcast(session: Session) -> pd.DataFrame:
    """One row per game per pitcher. Empty when no Statcast has been ingested."""
    rows = session.execute(
        PITCHER_GAME_SQL,
        {"k_events": list(STRIKEOUT_EVENTS), "bb_events": list(WALK_EVENTS)},
    ).mappings().all()
    if not rows:
        log.info("statcast.aggregate.empty")
        return pd.DataFrame()

    frame = pd.DataFrame(rows)
    for column in ("game_date_utc", "knowledge_time"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    for column in COUNT_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    frame["is_starter"] = frame["is_starter"].astype(bool)
    log.info(
        "statcast.aggregate.loaded",
        rows=len(frame),
        pitchers=int(frame["player_id"].nunique()),
        games=int(frame["game_id"].nunique()),
    )
    return frame.sort_values("knowledge_time").reset_index(drop=True)


__all__ = [
    "COUNT_COLUMNS",
    "STRIKEOUT_EVENTS",
    "WALK_EVENTS",
    "load_pitcher_statcast",
]
