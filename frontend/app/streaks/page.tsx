import { Badge } from "@/components/Badge";
import { NextGameStreakCards } from "@/components/NextGameStreakCards";
import { Section } from "@/components/StatBlock";
import { StreaksExplorer } from "@/components/StreaksExplorer";
import { UnavailableNotice } from "@/components/UnavailableNotice";
import { api } from "@/lib/api";
import { mediumDate } from "@/lib/format";
import type { TeamStreaks } from "@/lib/streaks";

export const metadata = { title: "Streaks" };

/**
 * Winning and losing streaks — research and display, never a model input.
 *
 * The page's whole posture is anti-narrative: every continuation rate is
 * shown beside what the team's pre-game Elo expectation already predicted
 * for those same games, because "wins 58% after L4" is only interesting
 * relative to "was expected to win 54% of those games anyway". The streak
 * *features* derived from this history go through the same walk-forward
 * ablation gate as every other candidate, and they are in the model only if
 * that gate says so — the current verdict is recorded in MODELING_PLAN.md.
 */
export default async function StreaksPage() {
  const result = await api.streaks();

  return (
    <div className="flex flex-col gap-4 sm:gap-5">
      <div>
        <h1 className="t-display">Winning &amp; losing streaks</h1>
        <p className="t-small mt-1.5 max-w-prose muted">
          Every streak is reconstructed from completed regular-season games,
          using only what was knowable before each first pitch. Continuation
          rates are shown next to the pre-game expectation for the same games —
          a streak is not a prophecy, and the gap between the two numbers is
          usually small. That gap is the finding.
        </p>
      </div>

      {!result.ok ? (
        <UnavailableNotice
          title="Streak history is unavailable"
          reason={result.message}
          requiredSource="backend at API_BASE_URL"
        />
      ) : !result.data.available ? (
        <UnavailableNotice
          title="Streak history is unavailable"
          reason={result.data.reason ?? "No completed games are ingested."}
        />
      ) : (
        <>
          <NextGameStreakCards
            games={result.data.next_games}
            minOccurrences={result.data.min_occurrences}
            shrinkageK={result.data.shrinkage_k}
          />

          <Section
            title="Current streaks"
            description={`Every team's active run, ${result.data.current_season}. Home and away streaks count consecutive results in that venue role only.`}
          >
            <CurrentStreaksTable teams={result.data.teams} />
          </Section>

          <StreaksExplorer
            teams={result.data.teams.map((t) => ({
              team_id: t.team_id,
              abbreviation: t.abbreviation,
              name: t.name,
            }))}
            seasons={result.data.seasons}
            currentSeason={result.data.current_season}
            minOccurrences={result.data.min_occurrences}
            shrinkageK={result.data.shrinkage_k}
            favoriteUnavailableReason={result.data.favorite_underdog.reason}
            expectationModel={result.data.expectation_model}
          />
        </>
      )}
    </div>
  );
}

function StreakChip({ value }: { value: string | null }) {
  if (!value) return <span className="subtle">—</span>;
  const winning = value.startsWith("W");
  return (
    <Badge tone={winning ? "home" : "danger"}>
      <span className="numeral">{value}</span>
    </Badge>
  );
}

function CurrentStreaksTable({ teams }: { teams: TeamStreaks[] }) {
  const ordered = [...teams].sort((a, b) => {
    const magnitude = (t: TeamStreaks) =>
      t.current_streak ? parseInt(t.current_streak.slice(1), 10) : 0;
    return magnitude(b) - magnitude(a);
  });
  return (
    <div className="scroll-x edge-cue">
      <table className="data sticky-label min-w-[560px]">
        <thead>
          <tr>
            <th scope="col">Team</th>
            <th scope="col">Streak</th>
            <th scope="col">Since</th>
            <th scope="col">Home</th>
            <th scope="col">Away</th>
            <th scope="col" className="num">
              Longest W
            </th>
            <th scope="col" className="num">
              Longest L
            </th>
            <th scope="col">Inside the streak</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((team) => (
            <tr key={team.team_id}>
              <th scope="row" className="font-normal">
                <span className="font-medium">{team.abbreviation}</span>{" "}
                <span className="muted hidden sm:inline">{team.name}</span>
              </th>
              <td>
                <StreakChip value={team.current_streak} />
              </td>
              <td className="tnum muted whitespace-nowrap">
                {team.current_streak_start
                  ? mediumDate(team.current_streak_start)
                  : "—"}
              </td>
              <td>
                <StreakChip value={team.home_streak} />
              </td>
              <td>
                <StreakChip value={team.away_streak} />
              </td>
              <td className="num tnum">{team.longest_win_streak || "—"}</td>
              <td className="num tnum">{team.longest_loss_streak || "—"}</td>
              <td className="muted">
                <span className="tnum">
                  {team.streak_games
                    .map(
                      (g) =>
                        `${g.result.split(" ")[1]} ${g.home ? "vs" : "@"} ${g.opponent}`,
                    )
                    .join(" · ") || "—"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
