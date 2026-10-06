import { describe, expect, it } from "vitest";
// @ts-expect-error -- node builtins aren't in the app tsconfig types; vitest runs on node.
import { readFileSync } from "node:fs";

// Read the files from disk: vitest doesn't process CSS, so `?raw` imports come back empty.
// (Base kept in a variable so Vite doesn't rewrite `new URL(..., import.meta.url)` to an asset.)
const here = import.meta.url;
const read = (name: string): string => readFileSync(decodeURIComponent(new URL(name, here).pathname), "utf8");

/** An unclosed `{` silently nests every later rule inside the previous at-rule (it happened:
 * the reduced-motion @media lost its `}` and the profile, match-detail and sample-report
 * rules after it only applied to reduced-motion users). Braces must balance per file. */
function braceDepth(css: string): number {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "").replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g, "");
  let depth = 0;
  for (const ch of stripped) {
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth < 0) return -1;
    }
  }
  return depth;
}

describe("stylesheets", () => {
  it.each(["polish.css", "styles.css", "roundForm.css"])("%s has balanced braces", (name) => {
    const css = read(name);
    expect(css.length).toBeGreaterThan(0);
    expect(braceDepth(css)).toBe(0);
  });
});
