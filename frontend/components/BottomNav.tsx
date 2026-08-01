"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * The primary navigation on a phone.
 *
 * A sticky top bar plus a stacked nav row ate about a third of an iPhone
 * viewport before this existed, and the links sat at the far end of a thumb's
 * reach. The same four destinations live here instead: fixed to the bottom,
 * 44pt targets, above the home indicator. Above `sm` it disappears entirely and
 * the header nav takes over.
 */

const ITEMS = [
  { href: "/", label: "Games", match: (p: string) => p === "/" || p.startsWith("/game") },
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
  const stroke = active ? "var(--accent)" : "currentColor";
  const common = {
    width: 20,
    height: 20,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke,
    strokeWidth: active ? 2 : 1.6,
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
      className="fixed inset-x-0 bottom-0 z-40 border-t sm:hidden"
      style={{
        borderColor: "var(--border)",
        background: "color-mix(in srgb, var(--surface) 82%, transparent)",
        backdropFilter: "blur(18px) saturate(1.7)",
        WebkitBackdropFilter: "blur(18px) saturate(1.7)",
      }}
    >
      <ul className="safe-b grid grid-cols-4">
        {ITEMS.map((item) => {
          const active = item.match(pathname);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className="relative flex min-h-[3.375rem] flex-col items-center justify-center gap-1 pt-2 pb-1.5"
                style={{ color: active ? "var(--accent)" : "var(--text-muted)" }}
              >
                {/* A short rule at the top edge of the active tab. On a bar
                    where the only other cue is a colour shift, the mark is what
                    survives glare, a colourblind reader and a glance. */}
                <span
                  aria-hidden
                  className="absolute inset-x-0 top-0 mx-auto h-[2px] rounded-full transition-all"
                  style={{
                    width: active ? "1.75rem" : "0",
                    opacity: active ? 1 : 0,
                    background: "var(--accent)",
                    transitionDuration: "var(--dur-base)",
                    transitionTimingFunction: "var(--ease-spring)",
                  }}
                />
                <Icon name={item.label} active={active} />
                <span
                  className="t-micro leading-none"
                  style={{ fontWeight: active ? 620 : 500 }}
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
