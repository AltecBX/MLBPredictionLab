/**
 * Skeletons shaped like what is coming — a title line, then card-sized panels
 * with the meter's silhouette inside. A wireframe of the real page holds the
 * layout still while it streams in; six anonymous grey slabs do not.
 */
export default function Loading() {
  return (
    <div className="flex flex-col gap-5" aria-busy>
      <div className="shimmer h-9 w-56 rounded-[var(--radius-md)]" />
      <div className="grid gap-3.5 sm:grid-cols-2 sm:gap-4 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="card flex flex-col gap-4 p-4">
            <div className="flex items-center justify-between">
              <div className="shimmer h-3.5 w-32 rounded-full" />
              <div className="shimmer h-5 w-16 rounded-full" />
            </div>
            <div className="flex flex-col gap-2.5">
              <div className="shimmer h-5 w-3/4 rounded-full" />
              <div className="shimmer h-5 w-2/3 rounded-full" />
            </div>
            <div className="flex items-end justify-between">
              <div className="shimmer h-8 w-16 rounded-[var(--radius-sm)]" />
              <div className="shimmer h-5 w-12 rounded-[var(--radius-sm)]" />
            </div>
            <div className="shimmer h-3 w-full rounded-full" />
            <div className="shimmer h-12 w-full rounded-[var(--radius-md)]" />
          </div>
        ))}
      </div>
    </div>
  );
}
