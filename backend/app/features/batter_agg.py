"""Per-game Statcast aggregates keyed by batter, and the batting orders behind them.

Same collapse as `statcast_agg`, for the other side of the plate: the pitch table
reduces once, in SQL, to one row per game per batter, so a walk-forward can slice
it thousands of times without touching two million pitches.

Two things make this frame wider than the pitcher one.

**Pitch families are columns, not rows.** A matchup feature needs a batter's
record against fastballs, breaking balls and offspeed separately, and keying the
frame by (game, batter, family) would triple the row count and turn one as-of
slice per batter into three. Held as columns instead, the slice is unchanged and
the families come along with it.

**Platoon is columns too**, for the same reason: what a hitter does against
left-handed pitching is a different quantity from what he does overall, and
tonight's starter throws one of the two.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger

log = get_logger(__name__)

# Savant's pitch_type codes, grouped into the three families a hitter actually
# prepares for. Grouping is not a convenience: a single pitcher throws six or
# seven distinct codes and a season gives perhaps 300 of the rarest, which is far
# below anything that stabilizes. Three families is where the sample lives.
#
# Codes outside these sets — pitchouts, eephus, unknown — fall into no family and
# are counted only in the overall totals. They are a fraction of a percent and
# inventing a family for them would be worse than leaving them out of the split.
PITCH_FAMILIES: dict[str, tuple[str, ...]] = {
    # Four-seam, sinker, cutter, and the legacy generic fastball code.
    "fb": ("FF", "SI", "FC", "FA", "FT"),
    # Slider, curve, knuckle-curve, sweeper, slurve, slow curve, screwball.
    "br": ("SL", "CU", "KC", "ST", "SV", "CS", "SC"),
    # Changeup, splitter, forkball, knuckleball.
    "os": ("CH", "FS", "FO", "KN"),
}

STRIKEOUT_EVENTS = ("strikeout", "strikeout_double_play", "strikeout_triple_play")
WALK_EVENTS = ("walk", "intent_walk")


def _family_columns() -> str:
    """Per-family count columns, generated so the three stay in lockstep."""
    parts: list[str] = []
    for key in PITCH_FAMILIES:
        parts += [
            f"COUNT(*) FILTER (WHERE p.is_pitch AND fam.family = '{key}')"
            f"                                                        AS {key}_pitches",
            f"COUNT(*) FILTER (WHERE p.is_pitch AND p.is_swing AND fam.family = '{key}')"
            f"                                                        AS {key}_swings",
            f"COUNT(*) FILTER (WHERE p.is_pitch AND p.is_whiff AND fam.family = '{key}')"
            f"                                                        AS {key}_whiffs",
            f"COUNT(b.id) FILTER (WHERE fam.family = '{key}')          AS {key}_bip",
            f"SUM(COALESCE(b.estimated_woba, p.woba_value))"
            f"  FILTER (WHERE p.pa_event IS NOT NULL AND fam.family = '{key}')"
            f"                                                        AS {key}_xwoba_num",
            f"SUM(p.woba_denom) FILTER (WHERE fam.family = '{key}')    AS {key}_pa",
        ]
    return ",\n      ".join(parts)


def _family_case() -> str:
    """A lateral that labels each pitch with its family exactly once."""
    whens = " ".join(
        f"WHEN p.pitch_type = ANY(ARRAY{list(codes)}::text[]) THEN '{key}'"
        for key, codes in PITCH_FAMILIES.items()
    )
    return f"CASE {whens} ELSE NULL END"


BATTER_GAME_SQL = text(
    f"""
    SELECT
      p.game_id,
      p.batter_id                                               AS player_id,
      g.game_date_utc,
      g.season,
      MAX(p.knowledge_time)                                     AS knowledge_time,
      MAX(s.team_id)                                            AS team_id,
      COALESCE(BOOL_OR(s.is_starter), FALSE)                    AS is_starter,
      MIN(s.batting_order_slot)                                 AS batting_order_slot,

      COUNT(*) FILTER (WHERE p.is_pitch)                        AS pitches,
      COUNT(*) FILTER (WHERE p.is_pitch AND p.is_swing)         AS swings,
      COUNT(*) FILTER (WHERE p.is_pitch AND p.is_whiff)         AS whiffs,
      COUNT(*) FILTER (WHERE p.is_pitch AND NOT p.is_in_zone)   AS out_of_zone,
      COUNT(*) FILTER (
        WHERE p.is_pitch AND NOT p.is_in_zone AND p.is_swing
      )                                                          AS chases,

      COUNT(*) FILTER (WHERE p.pa_event IS NOT NULL)            AS plate_appearances,
      COUNT(*) FILTER (WHERE p.pa_event = ANY(:k_events))       AS strikeouts,
      COUNT(*) FILTER (WHERE p.pa_event = ANY(:bb_events))      AS walks,
      SUM(p.woba_denom)                                         AS woba_denom,
      SUM(COALESCE(b.estimated_woba, p.woba_value))
        FILTER (WHERE p.pa_event IS NOT NULL)                   AS xwoba_num,

      -- Platoon. `pitcher_throws` is on the pitch row, so the split costs a
      -- filter rather than a join.
      SUM(p.woba_denom) FILTER (WHERE p.pitcher_throws = 'L')   AS pa_vs_l,
      SUM(COALESCE(b.estimated_woba, p.woba_value))
        FILTER (WHERE p.pa_event IS NOT NULL AND p.pitcher_throws = 'L')
                                                                AS xwoba_num_vs_l,
      SUM(p.woba_denom) FILTER (WHERE p.pitcher_throws = 'R')   AS pa_vs_r,
      SUM(COALESCE(b.estimated_woba, p.woba_value))
        FILTER (WHERE p.pa_event IS NOT NULL AND p.pitcher_throws = 'R')
                                                                AS xwoba_num_vs_r,

      COUNT(b.id)                                               AS balls_in_play,
      COUNT(*) FILTER (WHERE b.is_barrel)                       AS barrels,
      COUNT(*) FILTER (WHERE b.is_hard_hit)                     AS hard_hit,
      COUNT(b.launch_speed)                                     AS ev_count,
      SUM(b.launch_speed)                                       AS ev_sum,

      {_family_columns()}
    FROM pitches p
    JOIN games g ON g.id = p.game_id
    CROSS JOIN LATERAL (SELECT {_family_case()} AS family) fam
    LEFT JOIN batted_ball_events b
      ON  b.game_id      = p.game_id
      AND b.at_bat_index = p.at_bat_index
      AND b.pitch_number = p.pitch_number
    LEFT JOIN player_game_stats s
      ON  s.game_id   = p.game_id
      AND s.player_id = p.batter_id
      AND s.role      = 'batter'
    WHERE g.game_type = 'R'
    GROUP BY p.game_id, p.batter_id, g.game_date_utc, g.season
    """
)

# Pitcher arsenal: the same families, from the other side, plus how often each is
# thrown. Usage share is what turns a lineup's record against a family into a
# weight for tonight.
PITCHER_ARSENAL_SQL = text(
    f"""
    SELECT
      p.game_id,
      p.pitcher_id                                              AS player_id,
      g.game_date_utc,
      g.season,
      MAX(p.knowledge_time)                                     AS knowledge_time,
      COALESCE(BOOL_OR(s.is_starter), FALSE)                    AS is_starter,
      COUNT(*) FILTER (WHERE p.is_pitch)                        AS pitches,
      {_family_columns()}
    FROM pitches p
    JOIN games g ON g.id = p.game_id
    CROSS JOIN LATERAL (SELECT {_family_case()} AS family) fam
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

# Who started, where in the order, and when that became knowable. This is the
# substrate the expected lineup is projected from — never the lineup of the game
# being predicted, which LEAKAGE_PREVENTION.md §15 establishes is not knowable
# before first pitch anyway.
BATTING_ORDER_SQL = text(
    """
    SELECT
      s.game_id,
      s.team_id,
      s.player_id,
      s.batting_order_slot,
      s.game_date_utc,
      s.knowledge_time
    FROM player_game_stats s
    JOIN games g ON g.id = s.game_id
    WHERE s.role = 'batter'
      AND s.is_starter
      AND s.batting_order_slot BETWEEN 1 AND 9
      AND g.game_type = 'R'
    """
)

_BASE_COUNTS = (
    "pitches", "swings", "whiffs", "out_of_zone", "chases",
    "plate_appearances", "strikeouts", "walks", "woba_denom", "xwoba_num",
    "pa_vs_l", "xwoba_num_vs_l", "pa_vs_r", "xwoba_num_vs_r",
    "balls_in_play", "barrels", "hard_hit", "ev_count", "ev_sum",
)

FAMILY_COUNTS = tuple(
    f"{key}_{stat}"
    for key in PITCH_FAMILIES
    for stat in ("pitches", "swings", "whiffs", "bip", "xwoba_num", "pa")
)

BATTER_COUNTS = _BASE_COUNTS + FAMILY_COUNTS
ARSENAL_COUNTS = ("pitches",) + FAMILY_COUNTS


def _normalize(frame: pd.DataFrame, counts: tuple[str, ...]) -> pd.DataFrame:
    for column in ("game_date_utc", "knowledge_time"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    for column in counts:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    if "is_starter" in frame.columns:
        frame["is_starter"] = frame["is_starter"].astype(bool)
    return frame.sort_values("knowledge_time").reset_index(drop=True)


def load_batter_statcast(session: Session) -> pd.DataFrame:
    """One row per game per batter. Empty when no Statcast has been ingested."""
    rows = session.execute(
        BATTER_GAME_SQL,
        {"k_events": list(STRIKEOUT_EVENTS), "bb_events": list(WALK_EVENTS)},
    ).mappings().all()
    if not rows:
        log.info("statcast.batter_aggregate.empty")
        return pd.DataFrame()
    frame = _normalize(pd.DataFrame(rows), BATTER_COUNTS)
    log.info(
        "statcast.batter_aggregate.loaded",
        rows=len(frame),
        batters=int(frame["player_id"].nunique()),
    )
    return frame


def load_pitcher_arsenal(session: Session) -> pd.DataFrame:
    """One row per game per pitcher, with pitch-family usage."""
    rows = session.execute(PITCHER_ARSENAL_SQL).mappings().all()
    if not rows:
        return pd.DataFrame()
    frame = _normalize(pd.DataFrame(rows), ARSENAL_COUNTS)
    log.info("statcast.arsenal.loaded", rows=len(frame))
    return frame


def load_batting_orders(session: Session) -> pd.DataFrame:
    """Every completed start, by team, player and slot."""
    rows = session.execute(BATTING_ORDER_SQL).mappings().all()
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    for column in ("game_date_utc", "knowledge_time"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    frame["batting_order_slot"] = pd.to_numeric(frame["batting_order_slot"])
    log.info("lineups.orders.loaded", rows=len(frame))
    return frame.sort_values("knowledge_time").reset_index(drop=True)


__all__ = [
    "ARSENAL_COUNTS",
    "BATTER_COUNTS",
    "FAMILY_COUNTS",
    "PITCH_FAMILIES",
    "load_batter_statcast",
    "load_batting_orders",
    "load_pitcher_arsenal",
]
