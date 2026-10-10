/**
 * No rule outside a cascade layer may set a border colour.
 *
 * An unlayered rule beats every layered one whatever its selector, and
 * Tailwind's utilities and this app's component classes are all layered. For
 * most of the product's life `* { border-color: var(--border) }` sat outside a
 * layer, and every class and utility that set a border colour lost to it: the
 * active sort chip never drew its accent, the neutral badge never drew its
 * strong border, the coloured badges drew a grey hairline instead of none, and
 * no hover state changed a border. The default now lives in `@layer base`.
 * The computed colours themselves are checked in a browser in e2e/cascade.spec.ts.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/** Top-level blocks of a stylesheet: their prelude and their body. */
function topLevelBlocks(css: string): { prelude: string; body: string }[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const blocks: { prelude: string; body: string }[] = [];
  let depth = 0;
  let start = 0;
  let bodyStart = 0;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === "{") {
      if (depth === 0) bodyStart = i + 1;
      depth += 1;
    } else if (ch === "}") {
      depth -= 1;
      if (depth === 0) {
        const prelude = text.slice(start, bodyStart - 1).trim();
        blocks.push({ prelude, body: text.slice(bodyStart, i) });
        start = i + 1;
      }
    } else if (ch === ";" && depth === 0) {
      start = i + 1; // an @import or similar statement
    }
  }
  return blocks;
}

const SETS_BORDER_COLOR = /(^|[;{\s])border(-(top|right|bottom|left|inline|block)(-(start|end))?)?-color\s*:/;

describe("the cascade", () => {
  const css = readFileSync(path.join(process.cwd(), "app/globals.css"), "utf8");
  const blocks = topLevelBlocks(css);

  it("parses the stylesheet into its top-level blocks", () => {
    expect(blocks.some((b) => b.prelude.startsWith("@layer components"))).toBe(true);
    expect(blocks.some((b) => b.prelude.startsWith("@layer base"))).toBe(true);
  });

  it("sets no border colour outside a layer", () => {
    const offenders = blocks
      .filter((b) => !b.prelude.startsWith("@layer"))
      .filter((b) => SETS_BORDER_COLOR.test(b.body))
      .map((b) => b.prelude);
    expect(
      offenders,
      "An unlayered border colour overrides every class and utility that sets one. " +
        "Put the rule in @layer base (a default) or @layer components (a recipe).",
    ).toEqual([]);
  });

  it("keeps the default border colour, in the base layer", () => {
    const base = blocks.find((b) => b.prelude === "@layer base");
    expect(base?.body).toMatch(/\*\s*\{\s*border-color:\s*var\(--border\);?\s*\}/);
  });
});
