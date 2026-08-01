"""The five-second read.

Nine rows, always the same nine, always in the same order. A reader who opens a
game should be able to answer "who is favoured and why" without scrolling, and
should be able to compare two games by glancing at the same rows in each.

Every row is one of four states, and the distinction matters:

  HOME / AWAY   one side holds a measurable edge
  EVEN          measured, and the sides are level — this is a finding
  UNAVAILABLE   not measured, because the provider that would measure it is not
                configured. Never rendered as "even", which would be a lie.

The advantage rows are re-projections of contributions the model already
computed and stored — they are not a second opinion, and they sum back to the
same probability. The context rows (division, and the home/road split) are
descriptive and carry no probability weight, which is stated on the row.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.models import PredictionExplanation
from app.schemas.games import GameCard, MatchupSummaryRow

# Feature-key prefixes per summary row. Deliberately the same partition the
# ablation groups use, so what a reader sees grouped is what gets tested
# together (backtest/ablation.py FEATURE_GROUPS).
ROW_PREFIXES: dict[str, tuple[str, ...]] = {
    "starting_pitcher": ("sp_",),
    "bullpen": ("bp_",),
    "recent_form": ("off_form_delta", "_w30_", "_w14_"),
    "season_strength": ("elo_", "team_"),
}

# Rows whose provider is not configured. The env var is named on the row so the
# screen tells you what would enable it.
UNAVAILABLE_ROWS: dict[str, tuple[str, str]] = {
    "lineup": (
        "LINEUP_PROVIDER",
        "Batting orders weighted by expected plate appearances need a lineup feed.",
    ),
}

# Below this many probability points a difference is noise, not an edge.
EVEN_THRESHOLD_PP = 0.15


@dataclass(frozen=True, slots=True)
class _Contribution:
    key: str
    favors: str  # 'H' | 'A'
    pp: float


def _net_pp(contributions: list[_Contribution], prefixes: tuple[str, ...]) -> float:
    """Net probability points for the home side across matching features."""
    total = 0.0
    for c in contributions:
        if any(c.key.startswith(p) or p in c.key for p in prefixes):
            total += c.pp if c.favors == "H" else -c.pp
    return total


def _advantage(net_pp: float) -> str:
    if abs(net_pp) < EVEN_THRESHOLD_PP:
        return "EVEN"
    return "HOME" if net_pp > 0 else "AWAY"


def _row(
    key: str,
    label: str,
    net_pp: float,
    card: GameCard,
    detail: str | None = None,
) -> MatchupSummaryRow:
    advantage = _advantage(net_pp)
    team = (
        card.home.abbreviation
        if advantage == "HOME"
        else card.away.abbreviation
        if advantage == "AWAY"
        else None
    )
    return MatchupSummaryRow(
        key=key,
        label=label,
        advantage=advantage,
        team=team,
        # No `value` string: the component already renders magnitude_pp under
        # the team, and printing "4.6 pp" twice on one row is noise.
        value=None,
        magnitude_pp=round(abs(net_pp), 2),
        detail=detail,
    )


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}".lstrip("0")


def _home_away_row(card: GameCard) -> MatchupSummaryRow:
    """Each side in the role it is actually playing tonight.

    Descriptive, not a contribution: the model's own home/road term is inside
    `season_strength`. This row exists because a .640 home team visiting a .380
    road team is the single most legible fact on the card, and the overall
    records hide it.
    """
    home = card.home.home_record
    away = card.away.away_record
    if home is None or away is None or home.win_pct is None or away.win_pct is None:
        return MatchupSummaryRow(
            key="home_away",
            label="Home vs away form",
            advantage="UNAVAILABLE",
            detail="Neither side has completed games in this role yet.",
            available=False,
        )

    gap = home.win_pct - away.win_pct
    advantage = "EVEN" if abs(gap) < 0.02 else ("HOME" if gap > 0 else "AWAY")
    team = (
        card.home.abbreviation
        if advantage == "HOME"
        else card.away.abbreviation
        if advantage == "AWAY"
        else None
    )
    return MatchupSummaryRow(
        key="home_away",
        label="Home vs away form",
        advantage=advantage,
        team=team,
        value=(
            f"{card.home.abbreviation} {home.wins}-{home.losses} home · "
            f"{card.away.abbreviation} {away.wins}-{away.losses} away"
        ),
        magnitude_pp=None,
        detail=(
            f"{_pct(home.win_pct)} at home versus {_pct(away.win_pct)} on the road. "
            "Context — the model's own home/road term sits in season strength."
        ),
        is_context=True,
    )


def _division_row(card: GameCard) -> MatchupSummaryRow:
    """Standings position. Context only — Elo already carries team quality."""
    home, away = card.home.standing, card.away.standing
    if home is None or away is None:
        return MatchupSummaryRow(
            key="division",
            label="Division position",
            advantage="UNAVAILABLE",
            detail="Standings need completed games in the current season.",
            available=False,
        )

    def describe(abbr: str, standing) -> str:
        if standing.division_rank is None:
            return f"{abbr} —"
        gb = (
            f", {standing.games_behind} GB"
            if standing.games_behind not in (None, 0.0)
            else ", leading"
        )
        return f"{abbr} #{standing.division_rank}{gb}"

    # Rank is only comparable inside one division. "#4 in the NL Central" beats
    # "#5 in the AL East" in no sense whatsoever, and claiming an edge there
    # would be worse than saying nothing.
    same_division = (
        home.division_name is not None and home.division_name == away.division_name
    )
    better = None
    if same_division and home.division_rank is not None and away.division_rank is not None:
        if home.division_rank < away.division_rank:
            better = "HOME"
        elif away.division_rank < home.division_rank:
            better = "AWAY"

    return MatchupSummaryRow(
        key="division",
        label="Division position",
        advantage=better or "EVEN",
        team=(
            card.home.abbreviation
            if better == "HOME"
            else card.away.abbreviation
            if better == "AWAY"
            else None
        ),
        value=(
            f"{describe(card.home.abbreviation, home)} · "
            f"{describe(card.away.abbreviation, away)}"
        ),
        magnitude_pp=None,
        detail=(
            "Context only. Rank is a coarser encoding of the same results Elo and "
            "season win percentage already fit, so it carries no probability weight."
            + (
                ""
                if same_division
                else " These clubs are in different divisions, so their ranks are "
                "not comparable to each other."
            )
        ),
        is_context=True,
    )


def build_matchup_summary(
    card: GameCard, explanations: list[PredictionExplanation]
) -> list[MatchupSummaryRow]:
    """The nine-row summary, in fixed order."""
    prediction = card.prediction
    contributions = [
        _Contribution(e.feature_key, e.favors, float(e.contribution_pp))
        for e in explanations
    ]

    rows: list[MatchupSummaryRow] = [_home_away_row(card)]

    for key, label in (
        ("starting_pitcher", "Starting pitcher"),
        ("lineup", "Expected lineup"),
        ("bullpen", "Bullpen readiness"),
        ("recent_form", "Recent form"),
        ("season_strength", "Season strength"),
    ):
        if key in UNAVAILABLE_ROWS:
            source, reason = UNAVAILABLE_ROWS[key]
            rows.append(
                MatchupSummaryRow(
                    key=key,
                    label=label,
                    advantage="UNAVAILABLE",
                    detail=reason,
                    available=False,
                    required_source=source,
                )
            )
            continue
        if not contributions:
            rows.append(
                MatchupSummaryRow(
                    key=key,
                    label=label,
                    advantage="UNAVAILABLE",
                    detail="No prediction has been issued for this game.",
                    available=False,
                )
            )
            continue
        rows.append(_row(key, label, _net_pp(contributions, ROW_PREFIXES[key]), card))

    rows.append(_division_row(card))

    if prediction is None:
        rows.append(
            MatchupSummaryRow(
                key="probability",
                label="Win probability",
                advantage="UNAVAILABLE",
                detail="No prediction has been issued for this game.",
                available=False,
            )
        )
        rows.append(
            MatchupSummaryRow(
                key="confidence",
                label="Confidence and data",
                advantage="UNAVAILABLE",
                detail="No prediction has been issued for this game.",
                available=False,
            )
        )
        return rows

    home_favored = prediction.predicted_winner == "HOME"
    favored_prob = (
        prediction.home_win_prob if home_favored else prediction.away_win_prob
    )
    rows.append(
        MatchupSummaryRow(
            key="probability",
            label="Win probability",
            advantage="HOME" if home_favored else "AWAY",
            team=(
                card.home.abbreviation if home_favored else card.away.abbreviation
            ),
            value=f"{favored_prob * 100:.1f}%",
            magnitude_pp=round(abs(favored_prob - 0.5) * 100, 2),
            detail="Calibrated. This is the number every row above sums to.",
        )
    )
    rows.append(
        MatchupSummaryRow(
            key="confidence",
            label="Confidence and data",
            advantage="EVEN",
            value=(
                f"{prediction.confidence_score * 100:.0f}% confidence · "
                f"{prediction.data_completeness * 100:.0f}% of inputs available"
            ),
            magnitude_pp=None,
            detail=(
                "Confidence blends model agreement, sample sizes and input "
                "completeness. It is not the win probability."
            ),
            is_context=True,
        )
    )
    return rows


__all__ = ["build_matchup_summary", "EVEN_THRESHOLD_PP"]
