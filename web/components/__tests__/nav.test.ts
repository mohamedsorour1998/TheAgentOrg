/**
 * EVERY SCREEN IS REACHABLE, AND EVERY NAV ENTRY GOES SOMEWHERE.
 *
 * Both directions, for Lane I's reason in `endpoints.ts`: "a table naming a route
 * nobody built reads as a capability that exists, and a route absent from the
 * table is one Lane J will never call." Here the failure modes are a nav link to a
 * 404, and a screen that exists but which nobody can reach without typing a URL.
 *
 * READS THE FILESYSTEM, not a second list. A test comparing `NAV` against a
 * hand-written array of expected routes would pass while both drifted from the
 * directory they describe -- that is this repository's named pattern, an oracle
 * that cannot see the thing under test.
 */

import { readdirSync, existsSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { NAV } from "@/components/nav";

const ROUTES_DIR = join(process.cwd(), "app", "(routes)");
const AUTH_DIR = join(process.cwd(), "app", "(auth)");

/**
 * Route directories on disk. A directory counts only if it holds a `page.tsx` --
 * `runs/[runId]/` is a real route but is not a nav destination, and a directory
 * with no page is not a route at all.
 */
function routeDirectories(): string[] {
  return readdirSync(ROUTES_DIR, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    // A dynamic segment is not a top-level screen; it is reached from one.
    .filter((entry) => !entry.name.startsWith("["))
    .filter((entry) => existsSync(join(ROUTES_DIR, entry.name, "page.tsx")))
    .map((entry) => entry.name);
}

describe("navigation", () => {
  it("finds the routes directory at all", () => {
    // Without this the two tests below compare empty sets and pass vacuously,
    // which is the "this test would pin nothing" shape.
    expect(existsSync(ROUTES_DIR), `no route group at ${ROUTES_DIR}`).toBe(true);
    expect(routeDirectories().length).toBeGreaterThan(0);
    expect(NAV.length).toBeGreaterThan(0);
  });

  it("gives every nav entry a page that exists", () => {
    for (const item of NAV) {
      const segment = item.href.replace(/^\//, "");
      const page = join(ROUTES_DIR, segment, "page.tsx");
      expect(existsSync(page), `${item.href} is in the nav but ${page} does not exist`)
        .toBe(true);
    }
  });

  it("leaves no screen reachable only by typing its URL", () => {
    const navigable = new Set(NAV.map((item) => item.href.replace(/^\//, "")));

    // **THERE IS NO EXEMPTION LIST ANY MORE, AND THAT IS THE IMPROVEMENT.** This
    // used to carry `exempt = new Set(["signin"])`, because the sign-in screen
    // lived in this group and deliberately had no nav entry. Auth now lives in
    // `app/(auth)/`, which renders no nav at all -- so the rule became absolute:
    // every screen in THIS group is in the nav, with nothing to except.
    //
    // An exemption removed by construction beats an exemption guarded by a test.
    for (const dir of routeDirectories()) {
      expect(navigable.has(dir), `app/(routes)/${dir}/ has a page but no nav entry`)
        .toBe(true);
    }
  });

  it("keeps the auth screens OUT of the navigated group", () => {
    // What makes the absolute rule above true, asserted rather than assumed. If
    // either screen is moved back under `(routes)` it gains the dashboard's
    // header and nav -- which is the reported bug: `/signin` wearing Runs ·
    // Repositories · Costs · Account, four links whose only outcome is a
    // redirect back to the page you are already on.
    // ONE SCREEN, because GitHub sign-in IS sign-up: a first "Continue with
    // GitHub" derives a workspace, so a register page has nothing to ask for.
    for (const screen of ["signin"]) {
      expect(
        existsSync(join(AUTH_DIR, screen, "page.tsx")),
        `app/(auth)/${screen}/page.tsx is missing`,
      ).toBe(true);
      expect(
        existsSync(join(ROUTES_DIR, screen, "page.tsx")),
        `app/(routes)/${screen}/ is back, so that screen renders the dashboard nav`,
      ).toBe(false);
    }
  });
});
