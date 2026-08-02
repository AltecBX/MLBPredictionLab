/**
 * Server-side API client.
 *
 * A failed backend call surfaces as an explicit unavailable state in the UI —
 * never as an empty list that could be mistaken for "no games today".
 */

import type {
  BacktestReport,
  DiagnosticsSnapshot,
  FeatureSpec,
  GameDetail,
  GameListResponse,
} from "./types";

/**
 * A plain environment bag. Deliberately not `NodeJS.ProcessEnv`, which requires
 * `NODE_ENV` and so cannot be satisfied by the small literals these functions
 * are tested with; an index signature accepts both `process.env` and a literal.
 */
type EnvLike = Readonly<Record<string, string | undefined>>;

/**
 * The public domain suffix this app is itself served from, if any.
 *
 * Render sets `RENDER_EXTERNAL_HOSTNAME` to a service's own public hostname —
 * so a web service on `jerry-web.onrender.com` can learn that the public
 * suffix here is `.onrender.com`. That is what turns a sibling's bare service
 * name into an address that actually resolves.
 */
function publicDomainSuffix(env: EnvLike = process.env): string | null {
  const own = env.RENDER_EXTERNAL_HOSTNAME?.trim();
  if (own && own.includes(".")) return own.slice(own.indexOf("."));
  // Render sets RENDER=true everywhere; fall back to its default domain if the
  // hostname variable is ever absent.
  return env.RENDER ? ".onrender.com" : null;
}

/**
 * Normalise whatever the host platform hands us into a full base URL.
 *
 * Three shapes have to work:
 *
 *   https://api.example.com/api/v1   already complete
 *   https://api.example.com          missing the version prefix
 *   api:8000  /  127.0.0.1:8000      a container-network address
 *
 * And one that is not obvious. Render's `fromService … property: host` yields
 * a **bare internal service name** — `jerry-api-pwkc`, not the public FQDN.
 * On the free tier that address is unusable: a free service cannot *receive*
 * private network traffic, so every request to it is refused. Since Render's
 * public hostname for that service is the same name plus the platform's domain
 * suffix, and this app is told its own public hostname, the public address is
 * derivable. Doing that here keeps the blueprint one-click rather than making
 * someone paste a URL that Render only invents at create time.
 *
 * Anything genuinely wrong still surfaces as an explicit unavailable state
 * naming the URL that was tried.
 */
export function normalizeApiBaseUrl(raw: string, env: EnvLike = process.env): string {
  let url = raw.trim().replace(/\/+$/, "");
  if (!url) return "http://127.0.0.1:8000/api/v1";

  if (!/^https?:\/\//i.test(url)) {
    const [host, port] = url.split(":");
    const looksLocal = host === "localhost" || host === "127.0.0.1";
    const dotless = !host.includes(".");

    const suffix = publicDomainSuffix(env);
    if (dotless && !looksLocal && !port && suffix) {
      // A bare sibling service name on a platform with a public domain.
      url = `https://${host}${suffix}`;
    } else {
      // Loopback and container-network names are plain HTTP; a public dotted
      // domain is not.
      const isPrivate =
        looksLocal || dotless || host.endsWith(".internal") || host.endsWith(".local");
      url = `${isPrivate ? "http" : "https"}://${url}`;
    }
  }
  if (!/\/api\/v\d+$/.test(url)) url += "/api/v1";
  return url;
}

export const API_BASE_URL = normalizeApiBaseUrl(
  process.env.API_BASE_URL ?? "http://127.0.0.1:8000/api/v1",
);

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; message: string };

/**
 * Statuses worth trying again. A free-tier service that has been idle for
 * fifteen minutes returns these while it wakes, and it wakes in about a
 * minute — so treating the first one as final is what turns "the app is
 * starting" into "the app is broken" on the reader's screen.
 *
 * 404 and 422 are deliberately absent: those are answers, not outages.
 */
const RETRYABLE_STATUSES = new Set([408, 425, 429, 500, 502, 503, 504]);

/**
 * Delays between attempts. Totals about 21 seconds, which covers a cold start.
 *
 * `API_RETRY_ATTEMPTS` trims the list, and 0 disables retrying entirely. That
 * exists for the end-to-end suite, which points at a closed port on purpose to
 * exercise the unavailable states: there, every request fails instantly by
 * design and waiting 21 seconds to re-establish that proves nothing while
 * turning a three-minute job into a timeout. It is not a production knob.
 */
const ALL_RETRY_DELAYS_MS = [1_000, 4_000, 7_000, 9_000];

const RETRY_DELAYS_MS = (() => {
  const configured = Number.parseInt(process.env.API_RETRY_ATTEMPTS ?? "", 10);
  if (Number.isNaN(configured)) return ALL_RETRY_DELAYS_MS;
  return ALL_RETRY_DELAYS_MS.slice(0, Math.max(0, configured));
})();

const isRetryable = (status: number) => status === 0 || RETRYABLE_STATUSES.has(status);

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * True when a failure looks like a service that is still coming up rather than
 * one that is genuinely broken. Used to tell the reader to wait rather than
 * leaving them staring at a bare status code.
 */
export const looksLikeColdStart = (status: number) =>
  status === 0 || status === 502 || status === 503 || status === 504;

async function attempt<T>(
  path: string,
  init: (RequestInit & { revalidate?: number }) | undefined,
): Promise<ApiResult<T>> {
  const { revalidate = 30, ...rest } = init ?? {};
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...rest,
      headers: { Accept: "application/json", ...(rest.headers ?? {}) },
      next: { revalidate },
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const body = (await response.json()) as { detail?: string };
        if (body?.detail) detail = body.detail;
      } catch {
        /* body was not JSON; keep the status line */
      }
      return { ok: false, status: response.status, message: detail };
    }
    return { ok: true, data: (await response.json()) as T };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      message:
        error instanceof Error
          ? `Cannot reach the prediction API at ${API_BASE_URL}: ${error.message}`
          : "Cannot reach the prediction API.",
    };
  }
}

/**
 * Fetch, retrying while the API looks like it is waking rather than failing.
 *
 * The deployment this runs on sleeps after fifteen minutes idle and takes about
 * a minute to come back, so the *first* request after a quiet spell reliably
 * fails. Without retries that is indistinguishable, on screen, from the backend
 * being down — which is what a reader saw.
 *
 * A slow first paint is the right trade against a blank one. When every attempt
 * fails the honest unavailable state is still what renders; it just is not
 * reached on a wake-up that was always going to succeed.
 */
async function request<T>(
  path: string,
  init?: RequestInit & { revalidate?: number },
): Promise<ApiResult<T>> {
  let result = await attempt<T>(path, init);
  for (let i = 0; i < RETRY_DELAYS_MS.length && !result.ok; i += 1) {
    if (!isRetryable(result.status)) return result;
    await sleep(RETRY_DELAYS_MS[i]);
    result = await attempt<T>(path, init);
  }
  return result;
}

export const api = {
  games: (date: string, sort = "game_time") =>
    request<GameListResponse>(
      `/games?date=${encodeURIComponent(date)}&sort=${encodeURIComponent(sort)}`,
      { revalidate: 30 },
    ),
  game: (id: number | string) =>
    request<GameDetail>(`/games/${id}`, { revalidate: 30 }),
  backtest: () => request<BacktestReport>("/backtest/latest", { revalidate: 300 }),
  diagnostics: () =>
    request<DiagnosticsSnapshot>("/diagnostics", { revalidate: 15 }),
  features: () =>
    request<{
      feature_set_version: string;
      active: FeatureSpec[];
      deferred: FeatureSpec[];
      categories: Record<string, string>;
    }>("/meta/features", { revalidate: 3600 }),
};
