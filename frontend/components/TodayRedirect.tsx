"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { isBuilt, viewerToday } from "@/lib/window";

/**
 * Repoints the front page at the reader's own day.
 *
 * "/" is a file, baked on the build's UTC day, and UTC is already tomorrow
 * from 8 PM Eastern onward — a home-screen shortcut opened on a Sunday
 * evening was greeting the reader with Monday's slate. A static host cannot
 * look at the clock, so the browser does: when the reader's day differs from
 * the build's and has a page, swap over to it.
 *
 * `replace`, not `push` — the back button should leave the site, not return
 * the reader to the wrong day. And only when the target exists: on a site
 * that has not rebuilt for days the build's slate is the best page there is,
 * and staying put beats navigating to a 404.
 */
export function TodayRedirect({ buildDate }: { buildDate: string }) {
  const router = useRouter();
  useEffect(() => {
    const wanted = viewerToday();
    if (wanted !== buildDate && isBuilt(wanted, buildDate)) {
      router.replace(`/d/${wanted}/`);
    }
  }, [router, buildDate]);
  return null;
}
