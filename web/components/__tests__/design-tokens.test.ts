/**
 * EVERY `var(--token)` THE APP USES MUST BE DECLARED, because nothing else checks.
 *
 * **MEASURED 2026-09-16: THREE COMPONENTS STYLED THEIR ERROR MESSAGES WITH A TOKEN
 * THAT DOES NOT EXIST.** `StartRun`, `RepositoryPicker` and `SignUpForm` all wrote
 * `color: var(--rose)`. The token is `--refused`; `#fb7185` merely carries the word
 * "rose" in a trailing CSS comment in `globals.css`, so the colour's NAME was reached
 * for instead of the token's. CSS falls through to inherited text with no warning, so
 * every refusal
 * message on the sign-up, start-run and add-repository forms rendered as ordinary
 * prose — a message whose whole job is to look like a problem, not looking like one.
 *
 * **ALL FOUR WEB GATES PASSED ON IT.** `eslint` does not read CSS custom properties,
 * `tsc` sees a `string`, `vitest` ran no component, and `next build` compiles the
 * app without resolving a token that is only ever a runtime lookup. This is the
 * class of defect the gates structurally cannot see, so the test has to be written
 * by hand — the same argument as `refusals.test.ts` asserting over source text.
 *
 * **THE POSITIVE CONTROL IS NOT OPTIONAL AND IT IS THE HALF THAT MAKES THE ZERO
 * MEAN ANYTHING.** A broken extraction regex reports "0 undeclared tokens" exactly
 * as a clean tree does — this file's own named hazard. So `DECLARED` and `USED` are
 * both asserted non-empty with a count, the way the suite says `assert server.AGENTS`
 * rather than trusting an empty match.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const WEB = join(__dirname, "..", "..");

/** `--name:` in `globals.css` — the declarations. */
function declaredTokens(): Set<string> {
  const css = readFileSync(join(WEB, "app", "globals.css"), "utf8");
  return new Set([...css.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((m) => m[1]!));
}

function tsxFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === ".next") continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) tsxFiles(path, out);
    else if (entry.endsWith(".tsx")) out.push(path);
  }
  return out;
}

/** `var(--name)` across every component and route. */
function usedTokens(): Map<string, Set<string>> {
  const used = new Map<string, Set<string>>();
  for (const dir of ["components", "app"]) {
    for (const file of tsxFiles(join(WEB, dir))) {
      const source = readFileSync(file, "utf8");
      for (const match of source.matchAll(/var\((--[a-z0-9-]+)/g)) {
        const token = match[1]!;
        if (!used.has(token)) used.set(token, new Set());
        used.get(token)!.add(file.slice(WEB.length + 1));
      }
    }
  }
  return used;
}

describe("the design tokens the app reaches for exist", () => {
  it("finds the declarations and the uses at all", () => {
    // ANTI-VACUITY. A regex that matched nothing would make the real assertion
    // below pass against any tree, including one where every token is wrong.
    const declared = declaredTokens();
    const used = usedTokens();
    expect(declared.size, "no tokens parsed out of globals.css; this file would pin nothing").toBeGreaterThan(10);
    expect(used.size, "no var(--...) uses found in any .tsx; this file would pin nothing").toBeGreaterThan(10);
    // The one everybody uses, named so a change in `globals.css`'s shape is loud.
    expect(declared.has("--refused")).toBe(true);
  });

  it("uses no token that globals.css never declares", () => {
    const declared = declaredTokens();
    const undeclared = [...usedTokens().entries()]
      .filter(([token]) => !declared.has(token))
      .map(([token, files]) => `${token} used by ${[...files].sort().join(", ")}`);

    expect(
      undeclared,
      "a var(--token) with no declaration falls through to the inherited value in " +
        "silence -- eslint, tsc, vitest and next build all pass. Measured once: three " +
        "forms styled their error messages `var(--rose)`, which does not exist (the " +
        "token is `--refused`), so every refusal rendered as ordinary prose.",
    ).toEqual([]);
  });
});
