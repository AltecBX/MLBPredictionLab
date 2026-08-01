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

const GLYPH: Record<Theme, string> = { light: "☀", system: "◐", dark: "☾" };

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
        className="tap-sq rounded-md border text-base sm:hidden"
        style={{ borderColor: "var(--border)" }}
      >
        <span aria-hidden>{GLYPH[theme]}</span>
      </button>

      {/* Pointer devices get the explicit three-way control. */}
      <div
        role="group"
        aria-label="Color theme"
        className="hidden items-center rounded-md border p-0.5 sm:flex"
        style={{ borderColor: "var(--border)" }}
      >
        {(["light", "system", "dark"] as const).map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => choose(option)}
            aria-pressed={theme === option}
            aria-label={`${option[0].toUpperCase()}${option.slice(1)} theme`}
            title={`${option[0].toUpperCase()}${option.slice(1)} theme`}
            className={`rounded px-2.5 py-1.5 text-[0.68rem] capitalize transition-colors ${
              theme === option
                ? "bg-[var(--surface-sunken)] font-medium"
                : "muted hover:text-[var(--text)]"
            }`}
          >
            {option}
          </button>
        ))}
      </div>
    </>
  );
}
