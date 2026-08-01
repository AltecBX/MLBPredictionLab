"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * Desktop primary navigation.
 *
 * A segmented control rather than a row of links: the active destination gets a
 * filled chip, so the answer to "where am I" is a shape rather than a shade of
 * grey. Below `sm` this is hidden entirely and BottomNav takes over — the four
 * destinations are never duplicated on screen at once.
 */

export const NAV_ITEMS = [
  { href: "/", label: "Games", match: (p: string) => p === "/" || p.startsWith("/game") },
  {
    href: "/backtest",
    label: "Backtest",
    match: (p: string) => p.startsWith("/backtest"),
  },
  {
    href: "/methodology",
    label: "Methodology",
    match: (p: string) => p.startsWith("/methodology"),
  },
  {
    href: "/diagnostics",
    label: "Diagnostics",
    match: (p: string) => p.startsWith("/diagnostics"),
  },
] as const;

export function PrimaryNav() {
  const pathname = usePathname() ?? "/";

  return (
    <nav aria-label="Sections" className="hidden sm:block">
      <ul
        className="flex items-center gap-0.5 rounded-full border p-0.5"
        style={{ borderColor: "var(--border)", background: "var(--surface-inset)" }}
      >
        {NAV_ITEMS.map((item) => {
          const active = item.match(pathname);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className="t-small tap whitespace-nowrap rounded-full px-3 transition-colors"
                style={
                  active
                    ? {
                        background: "var(--surface-raised)",
                        color: "var(--text)",
                        fontWeight: 580,
                        boxShadow: "var(--shadow-1)",
                      }
                    : { color: "var(--text-muted)" }
                }
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
