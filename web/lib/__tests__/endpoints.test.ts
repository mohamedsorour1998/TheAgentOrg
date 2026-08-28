/**
 * THE TABLE AND THE FILESYSTEM MUST AGREE, IN BOTH DIRECTIONS.
 *
 * `ENDPOINTS` in `web/lib/endpoints.ts` is what Lane J reads to know what exists. Two
 * failures are possible and they are opposite:
 *
 *   * a TABLE ENTRY WITH NO FILE reads as a capability that exists. Lane K recorded
 *     exactly this about its absent gate scope: "a `gates:approve` nobody holds reads
 *     as a capability that exists, and the next person grants it and hunts for the
 *     broken route." Lane J would build a screen against a 404.
 *   * a FILE WITH NO TABLE ENTRY is an endpoint Lane J will never call, and an
 *     unlisted surface is one nobody reviews.
 *
 * So both directions are asserted, and the discovery is asserted non-empty first —
 * without that, a glob that matched nothing would satisfy every subset check
 * vacuously, which is this repository's most repeatable test defect.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { ENDPOINTS } from "../endpoints";

const API_ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "..",
  "app",
  "api",
);

/** Every `route.ts` on disk, as the path Next.js would serve it at. */
function discoverRoutes(dir: string, prefix = "/api"): { url: string; file: string }[] {
  const found: { url: string; file: string }[] = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...discoverRoutes(full, `${prefix}/${entry}`));
    } else if (entry === "route.ts") {
      found.push({ url: prefix, file: full });
    }
  }
  return found;
}

const ROUTES = discoverRoutes(API_ROOT);

/** Which HTTP methods a route file exports. Read from its source. */
function methodsIn(file: string): string[] {
  const source = readFileSync(file, "utf8");
  const methods: string[] = [];
  for (const method of ["GET", "POST", "PUT", "DELETE", "PATCH"]) {
    // Both spellings Next.js accepts: `export async function GET` and
    // `export const { GET, POST } = handlers`.
    if (
      new RegExp(`export async function ${method}\\b`).test(source) ||
      new RegExp(`export const \\{[^}]*\\b${method}\\b[^}]*\\} =`).test(source)
    ) {
      methods.push(method);
    }
  }
  return methods;
}

describe("the discovery itself", () => {
  it("found route files, so every check below is non-vacuous", () => {
    // THE GUARD THAT MAKES THE REST MEAN ANYTHING. `assert server.AGENTS,
    // "server.AGENTS is empty; this test would pin nothing"` is the operational form
    // of this all through the Python suite.
    expect(
      ROUTES.length,
      "no route.ts files were discovered; every assertion in this file would pin nothing",
    ).toBeGreaterThan(5);
  });

  it("found a method on every route file", () => {
    for (const route of ROUTES) {
      expect(methodsIn(route.file), route.url).not.toHaveLength(0);
    }
  });
});

describe("every table entry has a file", () => {
  it.each(ENDPOINTS.map((e) => [e.method, e.path] as const))(
    "%s %s exists on disk",
    (method, urlPath) => {
      const route = ROUTES.find((r) => r.url === urlPath);
      expect(route, `${urlPath} is in ENDPOINTS but no route.ts serves it`).toBeDefined();
      if (route) {
        expect(methodsIn(route.file)).toContain(method);
      }
    },
  );
});

describe("every file has a table entry", () => {
  it.each(ROUTES.map((r) => [r.url, r.file] as const))(
    "%s is declared in ENDPOINTS",
    (url, file) => {
      for (const method of methodsIn(file)) {
        const declared = ENDPOINTS.some(
          (e) => e.path === url && e.method === method,
        );
        expect(
          declared,
          `${method} ${url} exists on disk but is absent from ENDPOINTS, so Lane J will never call it`,
        ).toBe(true);
      }
    },
  );
});

describe("what the table claims about each route", () => {
  it("marks every mutating method as mutating, and no GET", () => {
    for (const endpoint of ENDPOINTS) {
      if (endpoint.method === "GET") {
        // A GET THAT MUTATES IS THE DEFECT `approve_server.do_GET` avoids by
        // rendering only: `/decide` is reachable by a back button, a bookmark or a
        // prefetch and must be inert when it is.
        expect(endpoint.mutates, `GET ${endpoint.path}`).toBe(false);
      } else {
        expect(endpoint.mutates, `${endpoint.method} ${endpoint.path}`).toBe(true);
      }
    }
  });

  it("authenticates everything except the two that structurally cannot", () => {
    // The Auth.js handler IS the sign-in, and `/api/session`'s whole answer may be
    // "nobody is signed in". Every other route requires a session, and this asserts
    // the exemption list has not grown — the `promote` job was exempted by name from
    // a test once, with a stale reason in a comment, and merged nothing while
    // reporting success.
    const unauthenticated = ENDPOINTS.filter((e) => !e.authenticated).map(
      (e) => `${e.method} ${e.path}`,
    );
    expect(unauthenticated.sort()).toEqual([
      "GET /api/auth/[...nextauth]",
      "GET /api/session",
      "POST /api/auth/[...nextauth]",
    ]);
  });

  it("declares exactly one route that can record a gate decision", () => {
    // ONE WRITE PATH TO A GATE. `approve_server` pins the equivalent with
    // `test_gates_resume_is_reached_from_exactly_one_place`, and the reasoning
    // carries: a second route that could open a gate is a second place every refusal
    // has to be reimplemented, and the one that gets missed is the one that ships.
    const approving = ENDPOINTS.filter((e) => e.path === "/api/approvals");
    expect(approving).toHaveLength(1);
    expect(approving[0]?.method).toBe("POST");
  });
});
