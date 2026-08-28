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
    // `signin` is deliberately absent from the nav: a person who is signed in has
    // no use for it, and a person who is not gets sent there. Every OTHER screen
    // must be in the nav.
    const exempt = new Set(["signin"]);

    for (const dir of routeDirectories()) {
      if (exempt.has(dir)) continue;
      expect(navigable.has(dir), `app/(routes)/${dir}/ has a page but no nav entry`)
        .toBe(true);
    }
  });

  it("keeps the signin exemption honest", () => {
    // The exemption above is only defensible while that screen exists and is
    // linked from somewhere else. If it is ever deleted, the exemption becomes a
    // name for nothing and this test says so.
    expect(existsSync(join(ROUTES_DIR, "signin", "page.tsx"))).toBe(true);
  });
});
