"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "jerry-theme";

function apply(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
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

  return (
    <div
      role="group"
      aria-label="Color theme"
      className="flex items-center rounded-md border p-0.5"
      style={{ borderColor: "var(--border)" }}
    >
      {(["light", "system", "dark"] as const).map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => choose(option)}
          aria-pressed={theme === option}
          title={`${option[0].toUpperCase()}${option.slice(1)} theme`}
          className={`rounded px-2 py-1 text-[0.68rem] capitalize transition-colors ${
            theme === option
              ? "bg-[var(--surface-sunken)] font-medium"
              : "muted hover:text-[var(--text)]"
          }`}
        >
          {option}
        </button>
      ))}
    </div>
  );
}
