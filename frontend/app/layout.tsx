import type { Metadata, Viewport } from "next";
import Link from "next/link";

import { BottomNav } from "@/components/BottomNav";
import { BrandMark } from "@/components/Brand";
import { PrimaryNav } from "@/components/PrimaryNav";
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
    { media: "(prefers-color-scheme: dark)", color: "#0d111a" },
  ],
};

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
          className="t-small sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-[var(--radius-md)] focus:border focus:bg-[var(--surface-overlay)] focus:px-3 focus:py-2 focus:shadow-[var(--shadow-3)]"
        >
          Skip to content
        </a>

        <header
          className="sticky top-0 z-30 border-b"
          style={{
            borderColor: "var(--border)",
            // A saturating blur is what keeps the header legible over a scrolling
            // card grid without resorting to an opaque bar, which would cut the
            // page in two.
            background: "color-mix(in srgb, var(--surface) 78%, transparent)",
            backdropFilter: "blur(14px) saturate(1.6)",
            WebkitBackdropFilter: "blur(14px) saturate(1.6)",
          }}
        >
          {/* One row on every screen. On a phone the four destinations move to
              the bottom bar, so nothing here wraps. */}
          <div className="safe-x mx-auto flex h-[calc(var(--header-h)-1px)] max-w-[1240px] items-center gap-3">
            <Link
              href="/"
              className="tap min-w-0 shrink items-center gap-2"
              aria-label="Jerry MLB Prediction Lab, home"
            >
              <BrandMark />
              <span className="t-heading min-w-0 truncate" style={{ fontWeight: 640 }}>
                <span className="sm:hidden">Jerry MLB</span>
                <span className="hidden sm:inline">Jerry MLB Prediction Lab</span>
              </span>
            </Link>

            <div className="ml-auto flex shrink-0 items-center gap-2 sm:gap-3">
              <PrimaryNav />
              <ThemeToggle />
            </div>
          </div>
        </header>

        <main
          id="main"
          className="safe-x mx-auto max-w-[1240px] pt-4 pb-8 sm:pt-7 sm:pb-10"
        >
          {children}
        </main>

        {/* pad-bottom-nav lives on the last element in the flow so the fixed bar
            never covers content — main already ends above it. */}
        <footer className="safe-x pad-bottom-nav mx-auto max-w-[1240px]">
          <hr className="rule-soft mb-5" />
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-8">
            <p className="t-micro max-w-[62ch] subtle">
              Data from the MLB Stats API and Baseball Savant. Probabilities are
              model estimates, not guarantees — no game is a lock. Every number on
              this site traces to a stored, timestamped prediction record.
            </p>
            <p className="t-micro flex shrink-0 items-center gap-1.5 subtle">
              <BrandMark size={14} />
              Jerry MLB Prediction Lab
            </p>
          </div>
        </footer>

        <BottomNav />
      </body>
    </html>
  );
}
