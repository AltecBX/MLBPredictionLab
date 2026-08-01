import Link from "next/link";

export default function NotFound() {
  return (
    <div className="surface p-10 text-center">
      <h1 className="text-lg font-semibold">Not found</h1>
      <p className="mt-2 text-sm muted">
        That page does not exist. It may have been a game id that is not in the
        database.
      </p>
      <Link
        href="/"
        className="mt-4 inline-block text-sm font-medium hover:underline"
        style={{ color: "var(--accent)" }}
      >
        ← Back to the game center
      </Link>
    </div>
  );
}
