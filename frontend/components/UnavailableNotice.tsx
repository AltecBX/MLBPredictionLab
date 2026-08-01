import type { ReactNode } from "react";

/**
 * Explicit unavailable state. Never a zero, never a placeholder number —
 * the reason and the source that would fix it are always named.
 */
export function UnavailableNotice({
  title,
  reason,
  requiredSource,
  phase,
  compact = false,
}: {
  title: string;
  reason: string;
  requiredSource?: string | null;
  phase?: number | null;
  compact?: boolean;
}) {
  return (
    <div
      className={`rounded-md border border-dashed ${compact ? "p-3" : "p-4"}`}
      style={{ borderColor: "var(--border-strong)", background: "var(--surface-sunken)" }}
    >
      <div className="flex items-start gap-2">
        <svg
          viewBox="0 0 16 16"
          aria-hidden
          className="mt-0.5 size-4 shrink-0 opacity-60"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <circle cx="8" cy="8" r="6.5" />
          <path d="M8 5v4M8 10.8v.5" strokeLinecap="round" />
        </svg>
        <div className="min-w-0">
          <p className="text-sm font-medium">{title}</p>
          <p className="mt-1 text-xs leading-relaxed muted">{reason}</p>
          {(requiredSource || phase) && (
            <p className="mt-1.5 text-[0.7rem] subtle">
              {requiredSource ? <>Required: <code className="font-mono">{requiredSource}</code></> : null}
              {requiredSource && phase ? " · " : null}
              {phase ? `Planned for Phase ${phase}` : null}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="surface p-10 text-center">
      <p className="text-sm font-medium">{title}</p>
      {children ? <div className="mt-2 text-xs muted">{children}</div> : null}
    </div>
  );
}
