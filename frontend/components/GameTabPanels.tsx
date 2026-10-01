"use client";

import { memo, useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { LAZY_PANELS } from "@/components/GameTabs";
import { Tabs, type TabDef } from "@/components/Tabs";
import { UnavailableNotice } from "@/components/UnavailableNotice";
import type { GameDetail } from "@/lib/types";

/**
 * The game page's tabs: one panel pre-rendered, nine rendered on demand.
 *
 * They used to be links carrying `?tab=`, resolved server-side, with only the
 * active panel rendered. A static export cannot do that: a query string is not
 * part of a file's path, so every tab would return the same pre-rendered
 * panel. The next answer was to render all ten panels into the page and show
 * one — instant switching, one file per game — and the cost of that was the
 * page: nine panels most readers never open, written into the HTML and again
 * into the hydration payload, were the larger part of three hundred kilobytes
 * a game.
 *
 * So the page now carries the first panel, the Prediction tab, pre-rendered as
 * `children`, and the other nine are rendered here from the game's detail file
 * (`/game/<id>/detail.json`, built beside the page) the first time each is
 * opened. The file is fetched once the page has settled, so the first tab a
 * reader opens is as instant as it was; it is eight kilobytes compressed, and
 * nothing is rendered from it until asked for. Until it arrives the panel says
 * it is loading; if it cannot be loaded the panel says that, with a way to try
 * again. It never shows a number it does not have.
 *
 * **Deep links still work.** The tab is read from the query string on mount
 * and written back on every change, so a shared `?tab=simulation` link opens
 * on the simulation panel and the back button still walks the tabs. A panel
 * once opened stays mounted, hidden, so the reader's scroll position within it
 * survives a round trip to another tab and find-in-page reaches what they have
 * looked at.
 */

/** What the detail file answers: the game, or why it could not be built. */
type DetailPayload = GameDetail | { available: false; reason: string };

type Loaded =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "ready"; detail: GameDetail }
  | { state: "unavailable"; reason: string }
  | { state: "failed"; message: string };

type IdleWindow = Window & {
  requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
  cancelIdleCallback?: (handle: number) => void;
};

export function GameTabPanels({
  tabs,
  basePath,
  detailUrl,
  children,
}: {
  tabs: TabDef[];
  basePath: string;
  /** The game's detail file, with the deployment's base path already applied. */
  detailUrl: string;
  /** The first tab's panel, pre-rendered on the server. */
  children: ReactNode;
}) {
  const first = tabs[0]?.key ?? "";
  const [active, setActive] = useState(first);
  // Lazy tabs that have been shown. They stay mounted once opened.
  const [opened, setOpened] = useState<string[]>([]);
  const [loaded, setLoaded] = useState<Loaded>({ state: "idle" });
  const inflight = useRef<Promise<void> | null>(null);
  const settled = useRef(false);

  const load = useCallback((): Promise<void> => {
    if (settled.current) return Promise.resolve();
    if (inflight.current) return inflight.current;
    setLoaded({ state: "loading" });
    const request = fetch(detailUrl)
      .then((response) =>
        response.ok ? (response.json() as Promise<DetailPayload>) : Promise.reject(new Error(`${response.status}`)),
      )
      .then((payload) => {
        settled.current = true;
        if ("card" in payload) setLoaded({ state: "ready", detail: payload });
        else setLoaded({ state: "unavailable", reason: payload.reason });
      })
      .catch((error: unknown) => {
        setLoaded({
          state: "failed",
          message: error instanceof Error ? error.message : String(error),
        });
      })
      .finally(() => {
        inflight.current = null;
      });
    inflight.current = request;
    return request;
  }, [detailUrl]);

  /** Make a tab the active one; a lazy tab is also marked opened and loaded. */
  const show = useCallback(
    (key: string) => {
      setActive(key);
      if (key !== first) {
        setOpened((current) => (current.includes(key) ? current : [...current, key]));
        void load();
      }
    },
    [first, load],
  );

  const wanted = useCallback(() => {
    const key = new URLSearchParams(window.location.search).get("tab");
    return key && tabs.some((t) => t.key === key) ? key : null;
  }, [tabs]);

  // On mount rather than during render: `location` does not exist while the
  // page is being prerendered, and reading it in render would also make the
  // server and client markup disagree.
  useEffect(() => {
    const key = wanted();
    if (key) show(key);
  }, [wanted, show]);

  // Keep the browser's history in step, so back walks the tabs the way it did
  // when each one was its own navigation.
  useEffect(() => {
    const onPop = () => show(wanted() ?? first);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [wanted, show, first]);

  // Fetch the file once the page has settled, so the first tab a reader opens
  // is instant. Eight kilobytes compressed; nothing is rendered until asked.
  useEffect(() => {
    const w = window as IdleWindow;
    if (w.requestIdleCallback) {
      const handle = w.requestIdleCallback(() => void load(), { timeout: 3_000 });
      return () => w.cancelIdleCallback?.(handle);
    }
    const handle = window.setTimeout(() => void load(), 1_500);
    return () => window.clearTimeout(handle);
  }, [load]);

  const select = (key: string) => {
    show(key);
    const url = key === first ? window.location.pathname : `?tab=${key}`;
    window.history.pushState(null, "", url);
  };

  return (
    <>
      <Tabs tabs={tabs} active={active} basePath={basePath} onSelect={select} />
      {tabs.map((tab) => {
        const isActive = tab.key === active;
        return (
          <div
            key={tab.key}
            hidden={!isActive}
            // The entrance runs on every switch, not only the first: the class
            // comes and goes with the tab, and a panel keeps its place.
            className={isActive ? "fade-in" : undefined}
          >
            {tab.key === first ? (
              children
            ) : opened.includes(tab.key) ? (
              <LazyPanel tab={tab} loaded={loaded} retry={load} />
            ) : null}
          </div>
        );
      })}
    </>
  );
}

/**
 * One on-demand panel. Memoised so switching tabs does not re-render every
 * panel that is open: `loaded` changes only when the file arrives.
 */
const LazyPanel = memo(function LazyPanel({
  tab,
  loaded,
  retry,
}: {
  tab: TabDef;
  loaded: Loaded;
  retry: () => Promise<void>;
}) {
  switch (loaded.state) {
    case "ready":
      return <>{LAZY_PANELS[tab.key]?.(loaded.detail) ?? null}</>;
    case "unavailable":
      return <UnavailableNotice title="This section is not available" reason={loaded.reason} />;
    case "failed":
      return (
        <div>
          <UnavailableNotice
            title="This section could not be loaded"
            reason={`The game's detail file did not load (${loaded.message}). It is published beside this page; a retry usually answers.`}
          />
          <button type="button" className="pill tap t-small mt-3 px-3" onClick={() => void retry()}>
            Try again
          </button>
        </div>
      );
    default:
      return (
        <p className="t-small muted" role="status" aria-live="polite">
          Loading {tab.label.toLowerCase()}…
        </p>
      );
  }
});
