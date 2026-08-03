"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * The primary navigation on a phone: a floating glass capsule above the home
 * indicator, which is where a modern iOS tab bar lives.
 *
 * A sticky top bar plus a stacked nav row ate about a third of an iPhone
 * viewport before this existed, and the links sat at the far end of a thumb's
 * reach. The same four destinations live here instead, as 44pt targets inside
 * a capsule that floats clear of the screen edges — content is visible sliding
 * beneath it, which is what makes the bar read as a layer rather than a wall.
 *
 * The outer <nav> is deliberately full-bleed and anchored to the bottom edge,
 * with the inset carried as padding rather than margin. That keeps the
 * element's own box reaching the physical bottom of the viewport — which is
 * what "within thumb reach" means, and what the mobile e2e suite measures.
 *
 * Above `sm` it disappears entirely and the header nav takes over.
 */

const ITEMS = [
  { href: "/", label: "Games", match: (p: string) => p === "/" || p.startsWith("/game") || p.startsWith("/d/") },
  { href: "/streaks", label: "Streaks", match: (p: string) => p.startsWith("/streaks") },
  { href: "/backtest", label: "Backtest", match: (p: string) => p.startsWith("/backtest") },
  {
    href: "/methodology",
    label: "Method",
    match: (p: string) => p.startsWith("/methodology"),
  },
  {
    href: "/diagnostics",
    label: "Health",
    match: (p: string) => p.startsWith("/diagnostics"),
  },
] as const;

function Icon({ name, active }: { name: string; active: boolean }) {
  const common = {
    width: 21,
    height: 21,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: active ? 2.1 : 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  if (name === "Games") {
    // A baseball: the ball with its two seams.
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="9" />
        <path d="M5.6 5.6c2.2 1.8 3.4 4 3.4 6.4s-1.2 4.6-3.4 6.4" />
        <path d="M18.4 5.6C16.2 7.4 15 9.6 15 12s1.2 4.6 3.4 6.4" />
      </svg>
    );
  }
  if (name === "Streaks") {
    // A run of results: three rising steps.
    return (
      <svg {...common}>
        <path d="M3 17.5h4.5v-4H12v-4h4.5V5H21" />
      </svg>
    );
  }
  if (name === "Backtest") {
    return (
      <svg {...common}>
        <path d="M3 3v18h18" />
        <path d="M7 15l4-5 3 3 5-7" />
      </svg>
    );
  }
  if (name === "Method") {
    return (
      <svg {...common}>
        <path d="M4 4h11l5 5v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z" />
        <path d="M14 4v5h5" />
        <path d="M7.5 13h7M7.5 16.5h4.5" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M3 12h4l2.5-6 4 12 2.5-6h5" />
    </svg>
  );
}

export function BottomNav() {
  const pathname = usePathname() ?? "/";

  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 flex justify-center px-3 sm:hidden"
      style={{
        // Padding, not margin: the nav's own box must reach the bottom edge.
        paddingBottom: "max(env(safe-area-inset-bottom, 0px), 0.625rem)",
        pointerEvents: "none",
      }}
    >
      <ul
        className="glass grid w-full max-w-[26rem] grid-cols-4 border"
        style={{
          pointerEvents: "auto",
          borderColor: "var(--border)",
          borderRadius: "var(--radius-xl)",
          boxShadow: "var(--shadow-3), inset 0 1px 0 var(--glow)",
        }}
      >
        {ITEMS.map((item) => {
          const active = item.match(pathname);
          return (
            <li key={item.href} className="p-1">
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className="relative flex min-h-[3.25rem] flex-col items-center justify-center gap-0.5 rounded-[calc(var(--radius-xl)-0.25rem)]"
                style={{
                  color: active ? "var(--accent)" : "var(--text-muted)",
                  background: active ? "var(--accent-soft)" : "transparent",
                  transition:
                    "background-color var(--dur-base) var(--ease-out), color var(--dur-base) var(--ease-out)",
                }}
              >
                <Icon name={item.label} active={active} />
                <span
                  className="t-micro leading-none"
                  style={{ fontWeight: active ? 640 : 500 }}
                >
                  {item.label}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
