"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { isBuilt, viewerToday } from "@/lib/window";

/**
 * The Today pill, aimed by the reader's clock rather than the build's.
 *
 * The site is static files stamped in UTC, and UTC is tomorrow from 8 PM
 * Eastern onward — so the build's own "today" pointed a Sunday-evening reader
 * at Monday. The browser knows what day it is for the person holding the
 * phone; this component asks it after mount (the prerender has no clock to
 * ask, and guessing during hydration would make server and client disagree).
 *
 * Until the answer arrives, the build's date stands in — the first paint is
 * wrong by at most the difference this component exists to correct, for a
 * frame or two. If the reader's date has no page — a site that has not been
 * rebuilt for days — the build date also stands in, because a Today link that
 * 404s is worse than one that is a day off.
 */
export function TodayLink({
  pageDate,
  buildDate,
}: {
  /** The date of the page this pill is rendered on. */
  pageDate: string;
  /** The build's notion of today — the prerender fallback. */
  buildDate: string;
}) {
  const [viewerDate, setViewerDate] = useState<string | null>(null);
  useEffect(() => {
    setViewerDate(viewerToday());
  }, []);

  const target =
    viewerDate && isBuilt(viewerDate, buildDate) ? viewerDate : buildDate;

  if (pageDate === target) {
    return (
      <span
        className="t-micro shrink-0 rounded-full px-2 py-0.5"
        style={{
          background: "var(--accent-soft)",
          color: "var(--accent)",
          fontWeight: 580,
        }}
      >
        Today
      </span>
    );
  }
  return (
    <Link
      href={`/d/${target}/`}
      className="pill tap t-micro shrink-0 px-2.5"
      style={{ fontWeight: 580 }}
    >
      Today
    </Link>
  );
}
