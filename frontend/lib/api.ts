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
 * Normalise whatever the host platform hands us into a full base URL.
 *
 * Render's `fromService` wiring yields a bare `host:port` with no scheme, and
 * it is easy to paste a root URL without the `/api/v1` suffix. Both are
 * unambiguous, so accept them rather than failing the whole site over a
 * missing prefix. Anything genuinely wrong still surfaces as an explicit
 * unavailable state naming the URL that was tried.
 */
export function normalizeApiBaseUrl(raw: string): string {
  let url = raw.trim().replace(/\/+$/, "");
  if (!url) return "http://127.0.0.1:8000/api/v1";
  if (!/^https?:\/\//i.test(url)) {
    // Loopback and Docker/Render private hostnames are plain HTTP; anything
    // with a public dotted domain is not.
    const host = url.split(":")[0];
    const isPrivate =
      host === "localhost" ||
      host === "127.0.0.1" ||
      !host.includes(".") ||
      host.endsWith(".internal") ||
      host.endsWith(".local");
    url = `${isPrivate ? "http" : "https"}://${url}`;
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

async function request<T>(
  path: string,
  init?: RequestInit & { revalidate?: number },
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
