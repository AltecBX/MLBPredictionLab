/**
 * Live game state, fetched by the reader's browser from MLB's own feed.
 *
 * The published site is static files on a CDN — that is what ended the 502s —
 * so anything that must be current at *view* time cannot be baked into the
 * HTML. Scores during a game are the sharpest case: they change pitch by
 * pitch, and a page built on the hour would present a two-hour-old number as
 * if it were the present.
 *
 * So the browser asks the source of record directly. `statsapi.mlb.com` is the
 * same feed the backend ingests from — our game ids ARE its gamePks — it is
 * served with `Access-Control-Allow-Origin: *`, and it needs no key. This is
 * real data from the real source, displayed as what it is; the product rule
 * against presenting fabricated data as real is exactly why the alternative —
 * showing the build-time snapshot during a live game — is the thing this
 * module exists to prevent.
 *
 * Display only, ever. Nothing fetched here feeds a prediction: predictions are
 * immutable records issued before first pitch, and a live score arriving after
 * first pitch fails the knowledge-time cut by definition.
 */

export interface LiveState {
  /** MLB's abstract state: Preview, Live, Final. */
  status: "Preview" | "Live" | "Final";
  detail: string;
  awayRuns: number | null;
  homeRuns: number | null;
  /** e.g. "Top 7" — present only while live. */
  inning: string | null;
}

export type LiveMap = Map<number, LiveState>;

interface ScheduleGame {
  gamePk: number;
  status?: { abstractGameState?: string; detailedState?: string };
  linescore?: {
    currentInning?: number;
    inningState?: string;
    teams?: {
      away?: { runs?: number };
      home?: { runs?: number };
    };
  };
  teams?: {
    away?: { score?: number };
    home?: { score?: number };
  };
}

const FEED = "https://statsapi.mlb.com/api/v1/schedule";

export async function fetchLiveStates(date: string): Promise<LiveMap> {
  const url = `${FEED}?sportId=1&date=${date}&hydrate=linescore`;
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`schedule feed ${response.status}`);
  const payload = (await response.json()) as {
    dates?: { games?: ScheduleGame[] }[];
  };

  const map: LiveMap = new Map();
  for (const game of payload.dates?.flatMap((d) => d.games ?? []) ?? []) {
    const abstract = game.status?.abstractGameState;
    if (abstract !== "Preview" && abstract !== "Live" && abstract !== "Final") {
      continue;
    }
    const line = game.linescore;
    const away = line?.teams?.away?.runs ?? game.teams?.away?.score ?? null;
    const home = line?.teams?.home?.runs ?? game.teams?.home?.score ?? null;
    const inning =
      abstract === "Live" && line?.currentInning
        ? `${line.inningState ?? ""} ${line.currentInning}`.trim()
        : null;
    map.set(game.gamePk, {
      status: abstract,
      detail: game.status?.detailedState ?? abstract,
      awayRuns: away,
      homeRuns: home,
      inning,
    });
  }
  return map;
}

/** Whether any game on the slate could change state right now. */
export function slateIsActive(
  firstPitches: string[],
  finals: boolean[],
  now: Date = new Date(),
): boolean {
  return firstPitches.some((iso, i) => {
    if (finals[i]) return false;
    const pitch = Date.parse(iso);
    if (Number.isNaN(pitch)) return false;
    // From 30 minutes before first pitch until seven hours after — past that
    // the schedule feed has long since gone Final.
    return now.getTime() > pitch - 30 * 60_000 && now.getTime() < pitch + 7 * 3_600_000;
  });
}
