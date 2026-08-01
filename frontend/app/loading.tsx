export default function Loading() {
  return (
    <div className="flex flex-col gap-4">
      <div className="h-8 w-64 animate-pulse rounded" style={{ background: "var(--track)" }} />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="h-64 animate-pulse rounded-[10px]"
            style={{ background: "var(--track)" }}
          />
        ))}
      </div>
    </div>
  );
}
