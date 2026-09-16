/**
 * THE SIGN-IN GATE — and the three ways it goes quietly wrong.
 *
 * **THE DEFECT THIS PINS WAS REPORTED FROM THE DEPLOYED APP**: opening the site
 * rendered the whole dashboard to a signed-out visitor — heading, nav, table frame
 * — and only the data inside came back refused. `app/page.tsx` even documented the
 * behaviour that did not exist (*"a signed-out visitor is sent on to `/signin` by
 * the runs screen itself"*). **A comment describing a guard is not a guard**, and
 * this repository has now found that in a workflow expression, in a test satisfied
 * by its own prose, and here.
 *
 * Three failure modes, none of which the four web gates can see:
 *
 * 1. **A path slips past the matcher.** It is a negative lookahead, so every
 *    exception is a hole shaped exactly like whatever it excludes. `signin` written
 *    without an anchor also excludes `/signinx`; the reference deployment measured
 *    precisely that on its own matcher. `tsc` sees a string.
 * 2. **`/signin` gets gated**, which is an infinite redirect: the page signed-out
 *    visitors are sent to, redirecting signed-out visitors.
 * 3. **`middleware.ts` comes back.** Next 16 deprecated that convention in favour
 *    of `proxy`; having BOTH files is a hard build error, and having only the old
 *    one is a warning this project treats as a failure.
 *
 * **THE MATCHER IS IMPORTED, NOT READ AS TEXT, AND THAT IS THE LOAD-BEARING
 * CHOICE.** The source contains `favicon\\.ico$` — a TypeScript escape — so a test
 * that read the file and built a `RegExp` from those bytes would compile a
 * DIFFERENT pattern from the one Next.js uses (a literal backslash instead of an
 * escaped dot) and would be testing a regex that exists nowhere. Importing gets the
 * parsed string, which is the one that ships.
 */

import { existsSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const WEB = join(__dirname, "..");

/** The matcher Next.js actually applies, compiled the way Next.js compiles it. */
async function gate(): Promise<RegExp> {
  const { config } = await import("../proxy");
  const pattern = config.matcher[0];
  expect(pattern, "proxy.ts exports no matcher; this file would pin nothing").toBeTruthy();
  return new RegExp(`^${pattern}$`);
}

describe("the sign-in gate", () => {
  it("is the `proxy` convention, and the old file is gone", async () => {
    // Both files present is a hard error; only the old one is a build warning.
    expect(
      existsSync(join(WEB, "middleware.ts")),
      "middleware.ts is back. Next 16 deprecated it in favour of proxy.ts, and " +
        "having both is a hard build error.",
    ).toBe(false);
    expect(existsSync(join(WEB, "proxy.ts"))).toBe(true);

    const mod = await import("../proxy");
    // Named `proxy`, not `middleware` — Next resolves the export by name, so a
    // wrongly-named one is a gate that is never invoked, silently.
    expect(typeof mod.proxy, "proxy.ts must export a function named `proxy`").toBe("function");
  });

  it("gates every private path, including ones nobody has written yet", async () => {
    const gated = await gate();
    for (const path of [
      "/",
      "/runs",
      "/runs/83f2906f-8e16-4244-a483-52757101b422",
      "/costs",
      "/repositories",
      "/account",
      // NOT YET A ROUTE, and that is the point: default-deny means a screen added
      // later arrives gated instead of arriving open.
      "/settings",
      "/admin/tenants",
      // THE ANCHOR. A bare `signin` exclusion would match this prefix and leave it
      // ungated — measured on the reference deployment's matcher.
      "/signinx",
    ]) {
      expect(gated.test(path), `${path} is NOT gated, so it renders to a signed-out visitor`).toBe(true);
    }
  });

  it("never gates the sign-in page, the API, or static assets", async () => {
    const gated = await gate();
    for (const path of [
      // An infinite redirect if gated.
      "/signin",
      "/signin/",
      // These answer 401 as JSON; an HTML redirect makes that an unparseable 200.
      "/api/session",
      "/api/runs",
      "/api/auth/callback",
      "/_next/static/chunk.js",
      "/_next/image/logo.png",
      "/favicon.ico",
    ]) {
      expect(gated.test(path), `${path} IS gated, and must not be`).toBe(false);
    }
  });
});
