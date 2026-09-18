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

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const WEB = join(__dirname, "..");

/**
 * Source with `//` and block comments removed and **every string body kept**.
 *
 * The string-preserving part is not incidental. `components/__tests__/refusals.test.ts`
 * uses a stripper that blanks string BODIES too — correct for its purpose, and
 * CLAUDE.md records it making a RED step INERT: the token under test became `""`
 * before the assertion ran, so reintroducing the defect changed nothing. Here the
 * thing being looked for IS a string (`href="/api/auth/logout"`), so a stripper of
 * that kind would delete the evidence rather than the commentary.
 *
 * Tracking the quote character is what makes it safe on this file: `"https://…"`
 * contains `//` and must not be read as the start of a comment.
 */
function withoutComments(source: string): string {
  let out = "";
  let quote: string | null = null;
  let i = 0;
  while (i < source.length) {
    const ch = source[i]!;
    const next = source[i + 1];
    if (quote !== null) {
      if (ch === "\\") {
        out += ch + (next ?? "");
        i += 2;
        continue;
      }
      if (ch === quote) quote = null;
      out += ch;
      i += 1;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      quote = ch;
      out += ch;
      i += 1;
      continue;
    }
    if (ch === "/" && next === "*") {
      const end = source.indexOf("*/", i + 2);
      i = end === -1 ? source.length : end + 2;
      continue;
    }
    if (ch === "/" && next === "/") {
      const end = source.indexOf("\n", i);
      i = end === -1 ? source.length : end;
      continue;
    }
    out += ch;
    i += 1;
  }
  return out;
}

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
      "/signupx",
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
      // REGISTER IS PUBLIC TOO, and omitting it is the sharper bug: somebody with
      // no account clicks "Create an account" and is redirected to the one screen
      // they cannot use.
      "/signup",
      "/signup/",
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

describe("every surface that decides 'is somebody signed in' knows about BOTH sessions", () => {
  /**
   * **`/account` SAID "Nobody is signed in" WHILE THE NAV SHOWED SIGN OUT AND THE
   * RUNS LOADED.** Reported from the deployed app: *"how come sign out is
   * available and I see runs"*.
   *
   * There are two sign-in paths and two cookies, because Cognito cannot federate
   * GitHub. `currentIdentity()` was taught the second one — so everything reading
   * authorisation through it kept working — and `GET /api/session` reads
   * authentication DIRECTLY, on purpose, because it has to tell *signed in but
   * unassigned* apart from *signed out*. That direct read knew only the Cognito
   * cookie.
   *
   * **The general form, and why this is a test rather than a fixed line:** adding
   * a second way to be signed in does not update the places that ask the question
   * their own way, and those places look correct in isolation. Each file below
   * decides whether somebody is signed in without going through
   * `currentIdentity()`, so each has to know both answers.
   */
  const SURFACES = [
    // The navigation gate: which cookie lets a page render at all.
    "proxy.ts",
    // The direct authentication read, for the three-state answer.
    "app/api/session/route.ts",
    // The resolver every authorised route depends on.
    "lib/session.ts",
  ];

  it.each(SURFACES)("%s consults the GitHub session too", (file) => {
    const source = withoutComments(readFileSync(join(WEB, file), "utf8"));

    // ANTI-VACUITY: these files are 40-60% commentary and every one of them
    // DISCUSSES the GitHub session at length, so a check over raw text would be
    // satisfied by the prose explaining the hazard. Confirm code survived.
    expect(source.trim().length, `${file}: stripped to nothing`).toBeGreaterThan(200);

    expect(
      source.includes("GITHUB_SESSION_COOKIE"),
      `${file} decides whether somebody is signed in and never looks at the GitHub ` +
        `session cookie. A GitHub sign-in reads as signed-out there, which is how ` +
        `/account came to say "Nobody is signed in" under a Sign out button.`,
    ).toBe(true);
  });
});

describe("signing out is reachable", () => {
  /**
   * **THE ROUTE EXISTED AND NOTHING LINKED TO IT.** Reported as "also need
   * logout" — and `app/api/auth/logout/route.ts` was already 60 careful lines:
   * it clears this app's cookie with the exact attributes the callback set it
   * with (a cookie deleted with a different `path` SURVIVES) and redirects to
   * Cognito's `/logout` so its session ends too. Correct, tested, and reachable
   * only by typing the URL.
   *
   * That is this repository's second named pattern — *a feature complete, tested,
   * and reached by nothing* — and the prescribed check for it is a grep for the
   * entry point. This is that grep, kept.
   */
  it("is linked from the app shell", () => {
    /**
     * **THE FIRST VERSION OF THIS TEST WAS SATISFIED BY ITS OWN COMMENTARY, AND
     * IT IS RECORDED RATHER THAN QUIETLY FIXED.** It asserted
     * `shell.includes("/api/auth/logout")`. RED: deleting the `href` from
     * `Shell.tsx` left this test GREEN, because the explanatory comment beside the
     * link names `app/api/auth/logout/route.ts` — so the substring was still
     * present with nothing linking anywhere. The mutation was caught only by the
     * sibling test, by accident.
     *
     * That is this repository's most repeatable failure ("the more carefully a
     * file explains what it must not do, the more likely a test for that thing is
     * satisfied by the explanation"), and files here are 40–60% commentary. The
     * fix is the standard one: assert over COMMENT-STRIPPED source.
     */
    const stripped = withoutComments(
      readFileSync(join(WEB, "components", "Shell.tsx"), "utf8"),
    );

    // ANTI-VACUITY: the stripper must still leave the component behind. A
    // stripper that ate everything would make the assertion below unfailable in
    // the other direction.
    expect(
      stripped.includes("export function Shell"),
      "the comment stripper removed the component itself; this test would pin nothing",
    ).toBe(true);

    expect(
      stripped.includes("/api/auth/logout"),
      "nothing in Shell.tsx links to /api/auth/logout, so signing out is reachable " +
        "only by typing the URL -- a correct answer nobody asks for",
    ).toBe(true);
  });

  it("uses a plain anchor, because the redirect leaves this origin", () => {
    const shell = readFileSync(join(WEB, "components", "Shell.tsx"), "utf8");
    // `<Link href="/api/auth/logout">` attempts a client-side navigation, which
    // cannot follow the cross-origin redirect to the Cognito domain: sign-out then
    // appears to do nothing while the session continues. The failure is silent,
    // which is why it is asserted rather than left to review.
    expect(
      /<Link[^>]*href=["']\/api\/auth\/logout/.test(shell),
      "Shell.tsx links sign-out with <Link>. That cannot follow the cross-origin " +
        "redirect to Cognito, so the click would end nothing while reporting success.",
    ).toBe(false);
    expect(/<a[\s\S]{0,200}?href="\/api\/auth\/logout"/.test(shell)).toBe(true);
  });
});
