import { GameCenter } from "@/components/GameCenter";
import { buildDates } from "@/lib/window";

/**
 * One page per date in the published window.
 *
 * `generateStaticParams` is what makes the arrows work without a server: each
 * date named here becomes a real file at build time. A date outside the window
 * has no page, which is why the arrows stop at the edge rather than linking to
 * a 404.
 */
export function generateStaticParams() {
  return buildDates().map((date) => ({ date }));
}

export const dynamicParams = false;

export default async function DatedGameCenterPage({
  params,
}: {
  params: Promise<{ date: string }>;
}) {
  const { date } = await params;
  return <GameCenter date={date} />;
}
