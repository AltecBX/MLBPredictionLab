"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "jerry-theme";

function apply(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

/** Order the phone button cycles through, so one thumb tap always advances. */
const CYCLE: Theme[] = ["system", "light", "dark"];

/**
 * Drawn rather than typed.
 *
 * The previous version set ☀ ◐ ☾ as text, which renders as a different weight,
 * size and baseline on every platform — and on some Android builds as a colour
 * emoji. Three 14px paths are identical everywhere and inherit currentColor.
 */
function ThemeIcon({ theme, size = 15 }: { theme: Theme; size?: number }) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.9,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  if (theme === "light") {
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2.5v2M12 19.5v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2.5 12h2M19.5 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" />
      </svg>
    );
  }
  if (theme === "dark") {
    return (
      <svg {...common}>
        <path d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.4 8.4 0 1 0 10.2 10.2Z" />
      </svg>
    );
  }
  // System: a circle lit from one side, which is what "follows the device" looks
  // like without resorting to the word.
  return (
    <svg {...common}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 3.5a8.5 8.5 0 0 1 0 17Z" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY) as Theme | null;
    if (stored) {
      setTheme(stored);
      apply(stored);
    }
  }, []);

  function choose(next: Theme) {
    setTheme(next);
    apply(next);
    window.localStorage.setItem(STORAGE_KEY, next);
  }

  const next = CYCLE[(CYCLE.indexOf(theme) + 1) % CYCLE.length];

  return (
    <>
      {/*
       * Phone: one 44pt button that cycles. Three 24px segments in a header
       * this narrow were both unreachable and unhittable.
       */}
      <button
        type="button"
        onClick={() => choose(next)}
        aria-label={`Color theme: ${theme}. Switch to ${next}.`}
        title={`Theme: ${theme} — tap for ${next}`}
        className="icon-btn tap-sq sm:hidden"
      >
        <ThemeIcon theme={theme} size={17} />
      </button>

      {/* Pointer devices get the explicit three-way control, matching the
          section nav beside it so the header reads as one row of controls. */}
      <div
        role="group"
        aria-label="Color theme"
        className="hidden items-center gap-0.5 rounded-full border p-0.5 sm:flex"
        style={{ borderColor: "var(--border)", background: "var(--surface-inset)" }}
      >
        {(["light", "system", "dark"] as const).map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => choose(option)}
            aria-pressed={theme === option}
            aria-label={`${option[0].toUpperCase()}${option.slice(1)} theme`}
            title={`${option[0].toUpperCase()}${option.slice(1)} theme`}
            className="tap-sq rounded-full transition-colors"
            style={
              theme === option
                ? {
                    background: "var(--surface-raised)",
                    color: "var(--text)",
                    boxShadow: "var(--shadow-1)",
                  }
                : { color: "var(--text-subtle)" }
            }
          >
            <ThemeIcon theme={option} />
          </button>
        ))}
      </div>
    </>
  );
}
