"use client";

import { useEffect, useState } from "react";

/**
 * Current conditions at the ballpark, at the top of the Game Center.
 *
 * The site is static files, so a "current" reading cannot be baked in — the
 * browser asks Open-Meteo at view time. That is the same provider the
 * backend's forecast ingest uses, keyless and CORS-open, so the number at the
 * top of the page and the forecasts in the database come from one source.
 *
 * The chip renders nothing until a reading arrives and disappears on failure
 * rather than showing a placeholder: ambient context earns its place by being
 * true, and a dash pretending to be a temperature is the kind of filler this
 * product bans. Display only — predictions never read from here.
 */
const REFRESH_MS = 10 * 60_000;

/** WMO weather codes, folded to the six states a glance can use. */
function describe(code: number): { label: string; kind: Kind } {
  if (code === 0) return { label: "Clear", kind: "sun" };
  if (code <= 2) return { label: "Partly cloudy", kind: "partly" };
  if (code === 3) return { label: "Overcast", kind: "cloud" };
  if (code === 45 || code === 48) return { label: "Fog", kind: "fog" };
  if (code >= 51 && code <= 67) return { label: "Rain", kind: "rain" };
  if (code >= 71 && code <= 77) return { label: "Snow", kind: "snow" };
  if (code >= 80 && code <= 82) return { label: "Showers", kind: "rain" };
  if (code >= 85 && code <= 86) return { label: "Snow showers", kind: "snow" };
  if (code >= 95) return { label: "Thunderstorms", kind: "storm" };
  return { label: "—", kind: "cloud" };
}

type Kind = "sun" | "partly" | "cloud" | "rain" | "snow" | "storm" | "fog";

interface Reading {
  tempF: number;
  windMph: number;
  code: number;
}

export function WeatherNow({
  latitude,
  longitude,
  place,
  context,
}: {
  latitude: number | null;
  longitude: number | null;
  /** Where the reading is from — always named, so 74° is 74° *somewhere*. */
  place: string;
  /** Why this park was chosen — surfaces in the tooltip so the choice explains itself. */
  context?: string;
}) {
  const [reading, setReading] = useState<Reading | null>(null);

  useEffect(() => {
    // The target can move mid-visit — a game ends and the chip repoints to the
    // next park. The old reading must not survive the move: better no chip for
    // a beat than Baltimore's temperature wearing Los Angeles's name.
    setReading(null);
    if (latitude == null || longitude == null) return;
    let cancelled = false;

    const load = async () => {
      try {
        const url =
          `https://api.open-meteo.com/v1/forecast?latitude=${latitude}` +
          `&longitude=${longitude}` +
          `&current=temperature_2m,weather_code,wind_speed_10m` +
          `&temperature_unit=fahrenheit&wind_speed_unit=mph`;
        const response = await fetch(url, { cache: "no-store" });
        if (!response.ok) return;
        const data = (await response.json()) as {
          current?: {
            temperature_2m?: number;
            weather_code?: number;
            wind_speed_10m?: number;
          };
        };
        const current = data.current;
        if (!cancelled && current?.temperature_2m != null) {
          setReading({
            tempF: Math.round(current.temperature_2m),
            windMph: Math.round(current.wind_speed_10m ?? 0),
            code: current.weather_code ?? 3,
          });
        }
      } catch {
        // No reading is rendered as no chip, never as a fake one.
      }
    };

    void load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [latitude, longitude]);

  if (!reading) return null;
  const { label, kind } = describe(reading.code);

  return (
    <p
      className="t-small flex min-w-0 items-center gap-1.5 muted"
      title={`Current conditions at ${place}${context ? ` — ${context}` : ""}`}
    >
      <WeatherGlyph kind={kind} />
      <span className="numeral" style={{ color: "var(--text)" }}>
        {reading.tempF}°
      </span>
      <span className="truncate">
        {label}
        {reading.windMph >= 8 ? ` · wind ${reading.windMph} mph` : ""}
        <span className="subtle"> · {place}</span>
      </span>
    </p>
  );
}

function WeatherGlyph({ kind }: { kind: Kind }) {
  const common = {
    width: 15,
    height: 15,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.9,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    style: { color: "var(--color-warn-400)", flexShrink: 0 },
  };
  if (kind === "sun") {
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="4.2" />
        <path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4M5 5l1.7 1.7M17.3 17.3 19 19M19 5l-1.7 1.7M6.7 17.3 5 19" />
      </svg>
    );
  }
  const cloud = (
    <path d="M7 18a4.5 4.5 0 1 1 .9-8.9A5.5 5.5 0 0 1 18.6 11 3.7 3.7 0 0 1 18 18Z" />
  );
  if (kind === "partly") {
    return (
      <svg {...common}>
        <path d="M15.2 5.6a3.4 3.4 0 0 1 3.3 2.5M16.7 3v1.2M20.9 7.2h1.2M19.9 4.1l-.9.9" />
        {cloud}
      </svg>
    );
  }
  if (kind === "rain") {
    return (
      <svg {...common}>
        {cloud}
        <path d="M9 20.5l.8-1.8M13 20.5l.8-1.8M17 20.5l.8-1.8" />
      </svg>
    );
  }
  if (kind === "snow") {
    return (
      <svg {...common}>
        {cloud}
        <path d="M9.5 20.3h.01M13.5 21h.01M17 20.3h.01" strokeWidth={2.6} />
      </svg>
    );
  }
  if (kind === "storm") {
    return (
      <svg {...common}>
        {cloud}
        <path d="M12.5 18.5 11 21h2.5l-1.4 2.4" />
      </svg>
    );
  }
  if (kind === "fog") {
    return (
      <svg {...common}>
        <path d="M4 14h13M6 17.5h13M4.5 21h11" />
      </svg>
    );
  }
  return <svg {...common}>{cloud}</svg>;
}
