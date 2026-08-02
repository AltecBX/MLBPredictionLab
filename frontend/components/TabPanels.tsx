"use client";

import { useEffect, useState, type ReactNode } from "react";

import { Tabs, type TabDef } from "@/components/Tabs";

/**
 * Tabs that survive having no server.
 *
 * They used to be links carrying `?tab=`, resolved server-side, with only the
 * active panel rendered. A static export cannot do that: a query string is not
 * part of a file's path, so every tab would return the same pre-rendered panel.
 *
 * So all ten panels are rendered into the page and the browser shows one. The
 * cost is page weight; the gains are that switching tabs is instant instead of
 * a navigation, and that the whole game — every panel — is in one file that a
 * CDN serves without waking anything up.
 *
 * **Deep links still work.** The tab is read from the query string on mount and
 * written back on every change, so a shared `?tab=simulation` link opens on the
 * simulation panel and the back button still walks the tabs. It is the same URL
 * contract as before, resolved one layer further out.
 *
 * Inactive panels are `hidden` rather than unmounted, so in-page anchors and
 * browser find-in-page still reach their content, and the reader's scroll
 * position within a panel survives a round trip to another tab.
 */
export function TabPanels({
  tabs,
  basePath,
  panels,
}: {
  tabs: TabDef[];
  basePath: string;
  panels: Record<string, ReactNode>;
}) {
  const fallback = tabs[0]?.key ?? "";
  const [active, setActive] = useState(fallback);

  // On mount rather than during render: `location` does not exist while the
  // page is being prerendered, and reading it in render would also make the
  // server and client markup disagree.
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("tab");
    if (wanted && tabs.some((t) => t.key === wanted)) setActive(wanted);
  }, [tabs]);

  // Keep the browser's history in step, so back walks the tabs the way it did
  // when each one was its own navigation.
  useEffect(() => {
    const onPop = () => {
      const wanted = new URLSearchParams(window.location.search).get("tab");
      setActive(wanted && tabs.some((t) => t.key === wanted) ? wanted : fallback);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [tabs, fallback]);

  const select = (key: string) => {
    setActive(key);
    const url = key === fallback ? window.location.pathname : `?tab=${key}`;
    window.history.pushState(null, "", url);
  };

  return (
    <>
      <Tabs tabs={tabs} active={active} basePath={basePath} onSelect={select} />
      {tabs.map((t) => (
        <div key={t.key} hidden={t.key !== active}>
          {panels[t.key]}
        </div>
      ))}
    </>
  );
}
