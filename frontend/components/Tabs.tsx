"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";

export interface TabDef {
  key: string;
  label: string;
  /** Shorter wording used on a phone, where the strip is swiped, not scanned. */
  shortLabel?: string;
}

/**
 * Link-driven tabs so every panel is server-rendered and deep-linkable.
 *
 * The client boundary buys exactly one thing: after navigating to a tab the
 * strip is re-rendered scrolled to the left, so on a phone the tab you just
 * tapped can be off-screen. This scrolls it back into view.
 */
export function Tabs({
  tabs,
  active,
  basePath,
}: {
  tabs: TabDef[];
  active: string;
  basePath: string;
}) {
  const activeRef = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [active]);

  return (
    <nav
      aria-label="Game sections"
      // Sticky directly under the header: ten tabs on a phone means the strip
      // is the primary way around this page, and scrolling a long panel should
      // not strand it at the top.
      className="scroll-x no-bar snap-x-strip sticky z-20 -mx-4 border-b px-4 backdrop-blur"
      style={{
        top: "var(--header-h)",
        borderColor: "var(--border)",
        background: "color-mix(in srgb, var(--surface-sunken) 92%, transparent)",
      }}
    >
      <ul className="flex min-w-max items-center gap-0.5">
        {tabs.map((tab) => {
          const isActive = tab.key === active;
          return (
            <li key={tab.key}>
              <Link
                ref={isActive ? activeRef : undefined}
                href={`${basePath}?tab=${tab.key}`}
                aria-current={isActive ? "page" : undefined}
                className={`tap whitespace-nowrap px-3 text-sm transition-colors ${
                  isActive ? "font-medium" : "muted hover:text-[var(--text)]"
                }`}
                style={
                  isActive
                    ? { color: "var(--accent)", boxShadow: "inset 0 -2px 0 var(--accent)" }
                    : undefined
                }
              >
                <span className="sm:hidden">{tab.shortLabel ?? tab.label}</span>
                <span className="hidden sm:inline">{tab.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
