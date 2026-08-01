"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="card p-8">
      <h1 className="text-lg font-semibold">Something went wrong</h1>
      <p className="mt-2 text-sm muted">{error.message}</p>
      <button
        type="button"
        onClick={reset}
        className="mt-4 rounded border px-3 py-1.5 text-sm"
        style={{ borderColor: "var(--border-strong)" }}
      >
        Try again
      </button>
    </div>
  );
}
