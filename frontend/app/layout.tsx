import type { Metadata, Viewport } from "next";
import Link from "next/link";

import { ThemeToggle } from "@/components/ThemeToggle";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Jerry MLB Prediction Lab",
    template: "%s · Jerry MLB Prediction Lab",
  },
  description:
    "Transparent, historically validated MLB win probabilities. Every prediction is an immutable, timestamped record with an explicit completeness and freshness state.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#11151f" },
  ],
};

const NAV = [
  { href: "/", label: "Game center" },
  { href: "/backtest", label: "Backtest" },
  { href: "/methodology", label: "Methodology" },
  { href: "/diagnostics", label: "Diagnostics" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Apply the stored theme before paint to avoid a flash. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem('jerry-theme');if(t&&t!=='system')document.documentElement.setAttribute('data-theme',t)}catch(e){}`,
          }}
        />
      </head>
      <body className="min-h-dvh">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-[var(--surface-raised)] focus:px-3 focus:py-2 focus:text-sm"
        >
          Skip to content
        </a>
        <header
          className="sticky top-0 z-40 border-b backdrop-blur"
          style={{
            borderColor: "var(--border)",
            background: "color-mix(in srgb, var(--surface) 88%, transparent)",
          }}
        >
          <div className="mx-auto flex max-w-[1180px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
            <Link href="/" className="flex items-baseline gap-2">
              <span className="text-[0.95rem] font-semibold tracking-tight">
                Jerry MLB Prediction Lab
              </span>
            </Link>
            <nav aria-label="Primary" className="order-3 w-full sm:order-none sm:w-auto">
              <ul className="scroll-x flex items-center gap-1 text-sm">
                {NAV.map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className="rounded px-2 py-1 muted transition-colors hover:bg-[var(--surface-sunken)] hover:text-[var(--text)]"
                    >
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
            <div className="ml-auto">
              <ThemeToggle />
            </div>
          </div>
        </header>

        <main id="main" className="mx-auto max-w-[1180px] px-4 py-6">
          {children}
        </main>

        <footer
          className="mx-auto max-w-[1180px] border-t px-4 py-6 text-xs subtle"
          style={{ borderColor: "var(--border)" }}
        >
          <p>
            Data from the MLB Stats API. Probabilities are model estimates, not
            guarantees — no game is a lock. Every number on this site traces to a
            stored, timestamped prediction record.
          </p>
        </footer>
      </body>
    </html>
  );
}
