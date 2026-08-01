import { Badge } from "./Badge";
import { StreakChip } from "./TeamContextLine";
import type { TeamRef } from "@/lib/types";

/**
 * One team's record, splits, streak and standings position.
 *
 * The streak lists its actual games — dates, opponents and scores — because
 * "W6" means very different things depending on who those six were against,
 * and that judgement belongs to the reader rather than to a number.
 */

function pct(value: number | null): string {
  if (value === null) return "—";
  return value.toFixed(3).replace(/^0/, "");
}

function ordinal(n: number): string {
  return ["", "1st", "2nd", "3rd", "4th", "5th"][n] ?? `${n}th`;
}

function Split({
  label,
  wins,
  losses,
  winPct,
  emphasis,
}: {
  label: string;
  wins: number;
  losses: number;
  winPct: number | null;
  emphasis: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-[0.7rem] uppercase tracking-wide subtle">{label}</dt>
      <dd className={`tnum mt-0.5 ${emphasis ? "text-base font-semibold" : "text-sm"}`}>
        {wins}-{losses}
        <span className="ml-1.5 text-xs subtle">{pct(winPct)}</span>
      </dd>
    </div>
  );
}

export function TeamStandingBlock({ team, isHome }: { team: TeamRef; isHome: boolean }) {
  const { home_record: home, away_record: away, streak, standing } = team;
  if (!home && !away && !streak && !standing) {
    return (
      <div className="min-w-0">
        <p className="text-sm font-medium">{team.team_name ?? team.name}</p>
        <p className="mt-1 text-xs muted">
          No completed games in this season yet, so there is nothing to report.
        </p>
      </div>
    );
  }

  const overall = home && away
    ? {
        wins: home.wins + away.wins,
        losses: home.losses + away.losses,
      }
    : null;
  const overallPct =
    overall && overall.wins + overall.losses > 0
      ? overall.wins / (overall.wins + overall.losses)
      : null;

  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <p className="text-sm font-medium">{team.team_name ?? team.name}</p>
        <span className="text-[0.7rem] subtle">{isHome ? "home" : "away"} tonight</span>
        {streak ? <StreakChip streak={streak} /> : null}
        {standing?.clinched_division ? <Badge tone="home">Clinched</Badge> : null}
        {standing?.eliminated ? <Badge tone="warn">Eliminated</Badge> : null}
        {standing?.in_playoff_position && !standing.clinched_division ? (
          <Badge tone="accent">In position</Badge>
        ) : null}
      </div>

      <dl className="mt-2 grid grid-cols-3 gap-3">
        {overall ? (
          <Split
            label="Overall"
            wins={overall.wins}
            losses={overall.losses}
            winPct={overallPct}
            emphasis={false}
          />
        ) : null}
        {home ? (
          <Split
            label="Home"
            wins={home.wins}
            losses={home.losses}
            winPct={home.win_pct}
            emphasis={isHome}
          />
        ) : null}
        {away ? (
          <Split
            label="Away"
            wins={away.wins}
            losses={away.losses}
            winPct={away.win_pct}
            emphasis={!isHome}
          />
        ) : null}
      </dl>

      {standing ? (
        <p className="mt-2 text-xs muted">
          {standing.division_rank !== null ? (
            <>
              {ordinal(standing.division_rank)} in the{" "}
              {standing.division_name ?? "division"}
              {standing.games_behind ? `, ${standing.games_behind} games back` : ""}
            </>
          ) : null}
          {standing.wildcard_rank !== null ? (
            <>
              {" · "}
              wild card #{standing.wildcard_rank}
              {standing.wildcard_games_behind !== null
                ? standing.wildcard_games_behind > 0
                  ? ` (${standing.wildcard_games_behind} back)`
                  : ` (${Math.abs(standing.wildcard_games_behind)} ahead)`
                : ""}
            </>
          ) : null}
          {standing.elimination_number !== null && !standing.eliminated ? (
            <>
              {" · "}
              <span
                title="Combined division-leader wins and own losses that would end this team's division chances."
              >
                elimination number {standing.elimination_number}
              </span>
            </>
          ) : null}
        </p>
      ) : null}

      {streak && streak.games.length ? (
        <div className="mt-2">
          <p className="text-[0.7rem] uppercase tracking-wide subtle">
            {streak.kind === "W" ? "Winning" : "Losing"} streak — {streak.length}{" "}
            {streak.length === 1 ? "game" : "games"}
            {streak.games.length < streak.length ? ` (last ${streak.games.length} shown)` : ""}
          </p>
          <ul className="mt-1 flex flex-col gap-0.5 text-xs">
            {streak.games.map((g) => (
              <li key={g.game_id} className="flex min-w-0 items-baseline gap-2">
                <span className="tnum shrink-0 subtle">{g.date.slice(5)}</span>
                <span className="min-w-0 truncate muted">
                  {g.is_home ? "vs" : "@"} {g.opponent}
                </span>
                <span className="tnum ml-auto shrink-0">
                  {g.runs_for}–{g.runs_against}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
