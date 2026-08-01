import Link from "next/link";

export interface TabDef {
  key: string;
  label: string;
}

/** Link-driven tabs so every panel is server-rendered and deep-linkable. */
export function Tabs({
  tabs,
  active,
  basePath,
}: {
  tabs: TabDef[];
  active: string;
  basePath: string;
}) {
  return (
    <nav aria-label="Game sections" className="scroll-x border-b" style={{ borderColor: "var(--border)" }}>
      <ul className="flex min-w-max items-center gap-0.5">
        {tabs.map((tab) => {
          const isActive = tab.key === active;
          return (
            <li key={tab.key}>
              <Link
                href={`${basePath}?tab=${tab.key}`}
                aria-current={isActive ? "page" : undefined}
                className={`inline-block whitespace-nowrap px-3 py-2 text-sm transition-colors ${
                  isActive ? "font-medium" : "muted hover:text-[var(--text)]"
                }`}
                style={
                  isActive
                    ? { color: "var(--accent)", boxShadow: "inset 0 -2px 0 var(--accent)" }
                    : undefined
                }
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
