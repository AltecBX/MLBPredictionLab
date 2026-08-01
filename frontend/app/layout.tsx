import type { Metadata, Viewport } from "next";
import Link from "next/link";

import { BottomNav } from "@/components/BottomNav";
import { ThemeToggle } from "@/components/ThemeToggle";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Jerry MLB Prediction Lab",
    template: "%s · Jerry MLB Prediction Lab",
  },
  description:
    "Transparent, historically validated MLB win probabilities. Every prediction is an immutable, timestamped record with an explicit completeness and freshness state.",
  applicationName: "Jerry MLB Lab",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    title: "Jerry MLB",
    // Translucent keeps the status bar readable in both themes while letting
    // the sticky header sit under it.
    statusBarStyle: "black-translucent",
  },
  formatDetection: {
    // "8/1" and W–L records are not phone numbers; iOS otherwise linkifies them.
    telephone: false,
    date: false,
  },
  other: {
    // Next emits the modern `mobile-web-app-capable`, which iOS 18 and later
    // honour. Anything older still reads only the apple-prefixed name, and
    // without it "Add to Home Screen" opens inside Safari's chrome.
    "apple-mobile-web-app-capable": "yes",
  },
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: [{ url: "/apple-icon.png", sizes: "180x180", type: "image/png" }],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Pinch-zoom stays enabled — capping it is an accessibility regression, and
  // the layout no longer needs the crutch.
  maximumScale: 5,
  // Lets the page paint under the notch and the home indicator; the safe-area
  // utilities put the padding back where it matters.
  viewportFit: "cover",
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
          className="sticky top-0 z-30 border-b backdrop-blur"
          style={{
            borderColor: "var(--border)",
            background: "color-mix(in srgb, var(--surface) 96%, transparent)",
          }}
        >
          {/* One row on every screen. On a phone the four destinations move to
              the bottom bar, so nothing here wraps. */}
          <div className="safe-x mx-auto flex max-w-[1180px] items-center gap-x-5 py-2 sm:py-3">
            <Link href="/" className="tap min-w-0 shrink">
              <span className="truncate text-[0.95rem] font-semibold tracking-tight">
                <span className="sm:hidden">Jerry MLB Lab</span>
                <span className="hidden sm:inline">Jerry MLB Prediction Lab</span>
              </span>
            </Link>
            <nav aria-label="Primary" className="hidden sm:block">
              <ul className="flex items-center gap-1 text-sm">
                {NAV.map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className="tap whitespace-nowrap rounded px-2.5 muted transition-colors hover:bg-[var(--surface-sunken)] hover:text-[var(--text)]"
                    >
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
            <div className="ml-auto shrink-0">
              <ThemeToggle />
            </div>
          </div>
        </header>

        <main id="main" className="safe-x mx-auto max-w-[1180px] pt-4 pb-6 sm:pt-6">
          {children}
        </main>

        {/* pad-bottom-nav lives on the last element in the flow so the fixed bar
            never covers content — main already ends above it. */}
        <footer
          className="safe-x pad-bottom-nav mx-auto max-w-[1180px] border-t pt-6 text-xs subtle"
          style={{ borderColor: "var(--border)" }}
        >
          <p>
            Data from the MLB Stats API. Probabilities are model estimates, not
            guarantees — no game is a lock. Every number on this site traces to a
            stored, timestamped prediction record.
          </p>
        </footer>

        <BottomNav />
      </body>
    </html>
  );
}
