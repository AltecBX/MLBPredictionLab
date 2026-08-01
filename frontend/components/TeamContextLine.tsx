import type { StandingSummary, StreakSummary, TeamRef } from "@/lib/types";

/**
 * The context a reader wants in the five seconds they spend on a card:
 * how this team does *in the role it is playing tonight*, and whether it is
 * currently hot or cold.
 *
 * The role-specific record is the point. A team's overall record is already on
 * the row above it; "18-36 away" is what actually bears on an away game, and it
 * is frequently nothing like the overall number.
 *
 * None of this feeds the model. Streak length in particular is deliberately
 * context only — see services/team_context.py for why.
 */

function pct(value: number | null): string | null {
  if (value === null) return null;
  // Baseball convention: .586, no leading zero.
  return value.toFixed(3).replace(/^0/, "");
}

export function StreakChip({ streak }: { streak: StreakSummary }) {
  const winning = streak.kind === "W";
  return (
    <span
      className="tnum rounded px-1 text-[0.65rem] font-semibold"
      title={`${winning ? "Won" : "Lost"} ${streak.length} straight`}
      style={{
        color: winning ? "var(--home)" : "var(--away)",
        background: `color-mix(in srgb, ${
          winning ? "var(--home)" : "var(--away)"
        } 12%, transparent)`,
      }}
    >
      {streak.label}
    </span>
  );
}

/** "2nd AL Central · 4.5 GB", or the clinched/eliminated state when decided. */
export function standingLabel(standing: StandingSummary): string | null {
  if (standing.division_rank === null) return null;
  const division = standing.division_name ?? "division";
  const ordinal = ["", "1st", "2nd", "3rd", "4th", "5th"][standing.division_rank] ??
    `${standing.division_rank}th`;
  const base = `${ordinal} ${division}`;
  if (standing.clinched_division) return `${base} · clinched`;
  if (standing.eliminated) return `${base} · eliminated`;
  if (standing.games_behind !== null && standing.games_behind > 0) {
    return `${base} · ${standing.games_behind} GB`;
  }
  return base;
}

export function TeamContextLine({
  team,
  isHome,
  className = "",
}: {
  team: TeamRef;
  isHome: boolean;
  className?: string;
}) {
  const role = isHome ? team.home_record : team.away_record;
  const roleLabel = isHome ? "home" : "away";
  if (!role && !team.streak) return null;

  const rolePct = role ? pct(role.win_pct) : null;

  return (
    <span className={`flex shrink-0 items-center gap-1.5 text-[0.7rem] ${className}`}>
      {role ? (
        <span className="tnum subtle" title={`${role.wins}-${role.losses} at ${roleLabel}`}>
          {role.wins}-{role.losses} {roleLabel}
          {rolePct ? <span className="ml-1 opacity-70">{rolePct}</span> : null}
        </span>
      ) : null}
      {team.streak ? <StreakChip streak={team.streak} /> : null}
    </span>
  );
}
