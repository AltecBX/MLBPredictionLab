"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

/**
 * Recover from a sleeping backend without the reader doing anything.
 *
 * The deployment sleeps after fifteen minutes idle and can take the better part
 * of a minute to wake. Retrying inside the server request handles a brief blip,
 * but stretching that to cover a full cold start would mean a blank sixty-second
 * first paint — trading one bad experience for another.
 *
 * So the page renders immediately, says plainly that the service is waking, and
 * this keeps calling `router.refresh()` until it answers. The reader watches a
 * counter instead of a dead page, and the moment the API is up the real slate
 * appears on its own. No reload, no guessing when to try again.
 *
 * It gives up after `MAX_ATTEMPTS`. A backend that has not answered in two and a
 * half minutes is not waking, it is down, and a spinner that never stops is a
 * lie about that. When it stops, the notice stops claiming the service is on its
 * way.
 */
const INTERVAL_MS = 5_000;
const MAX_ATTEMPTS = 30;

export function WakeRetry() {
  const router = useRouter();
  const [attempts, setAttempts] = useState(0);

  useEffect(() => {
    if (attempts >= MAX_ATTEMPTS) return;
    const id = setTimeout(() => {
      setAttempts((n) => n + 1);
      router.refresh();
    }, INTERVAL_MS);
    return () => clearTimeout(id);
  }, [attempts, router]);

  const exhausted = attempts >= MAX_ATTEMPTS;
  const elapsed = Math.round((attempts * INTERVAL_MS) / 1000);

  return (
    <p className="t-small mt-3 flex items-center gap-2 muted" role="status">
      {!exhausted ? (
        <span
          aria-hidden
          className="inline-block size-2 shrink-0 animate-pulse rounded-full"
          style={{ background: "var(--accent)" }}
        />
      ) : null}
      {exhausted
        ? `Still no answer after ${elapsed} seconds of retrying. The service looks down rather than asleep.`
        : `Retrying automatically — ${attempts === 0 ? "checking now" : `${attempts} ${attempts === 1 ? "check" : "checks"}, ${elapsed}s`}. This page will fill in on its own.`}
    </p>
  );
}
