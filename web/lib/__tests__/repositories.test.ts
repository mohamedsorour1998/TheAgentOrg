/**
 * THE REPOSITORY NAME PATTERN. Anchored, and that is the whole test.
 *
 * This value is compared against `RunFacts.repositoryFullName` to authorise a gate
 * approval, and it reaches a URL. `github_ops._ISSUE_REF` records what unanchored
 * patterns cost: without both anchors `7-extra`, `7 7`, `#7x` and `1-2` all yielded
 * issue 7, "which is a comment written on a real issue nobody named" — and the next
 * thing that code did with the answer was WRITE.
 *
 * The pattern is re-declared here rather than imported from the route, because a route
 * file cannot be imported without Next's request context. That makes this a SECOND
 * DECLARATION, which is the thing this repository normally refuses — so
 * `the pattern matches the route's own` reads the route's source and compares, and
 * asserts it found the line first.
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

/** Mirrors `web/app/api/repositories/route.ts`. Kept honest by the last test here. */
const FULL_NAME = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/;

function isFullName(value: string): boolean {
  return FULL_NAME.test(value);
}

describe("what a repository name is", () => {
  it.each([
    "mohamedsorour1998/auth-service",
    "acme/api",
    "a/b",
    "with.dots/and-dashes",
    "Under_scores/Are_Fine",
  ])("accepts %j", (name) => {
    expect(isFullName(name)).toBe(true);
  });
});

describe("what it is not — the anchors doing the work", () => {
  it.each([
    ["../../etc/passwd", "a traversal, and it has two slashes"],
    ["acme/auth extra", "trailing text after a valid name"],
    ["extra acme/auth", "leading text before a valid name"],
    ["acme/auth\nother/repo", "a newline, so two names in one string"],
    ["acme/auth/nested", "two slashes"],
    ["acme", "no slash at all"],
    ["/auth-service", "an empty owner"],
    ["acme/", "an empty name"],
    ["", "empty"],
    ["   ", "whitespace"],
    ["acme/auth;DROP TABLE repository", "punctuation this class excludes"],
    ["acme/auth?x=1", "a query string"],
    ["acme/auth#frag", "a fragment"],
    ["https://github.com/acme/auth", "a whole URL"],
  ])("refuses %j — %s", (name) => {
    expect(isFullName(name)).toBe(false);
  });

  it("refuses a name that CONTAINS a valid one", () => {
    // The unanchored version of this pattern would accept every one of these, which
    // is exactly the `_ISSUE_REF` failure: a value that resolves to a real resource
    // nobody named.
    for (const hostile of [
      "x acme/api",
      "acme/api x",
      "\tacme/api",
      "acme/api\r",
    ]) {
      expect(isFullName(hostile), hostile).toBe(false);
    }
  });
});

describe("the second declaration is kept honest", () => {
  it("matches the pattern the route actually uses", () => {
    // A SECOND DECLARATION IS ONLY ACCEPTABLE IF SOMETHING COMPARES THEM. Without
    // this, the route's pattern could be widened and every test above would keep
    // passing against a copy nothing runs — this repository's named pattern, in a
    // test file.
    const source = readFileSync(
      new URL("../../app/api/repositories/route.ts", import.meta.url),
      "utf8",
    );
    const found = source.match(/const FULL_NAME = (\/.*\/);/);
    // The matcher must have matched. `assert server.AGENTS` is the operational form
    // of this everywhere in the Python suite.
    expect(found, "the route's FULL_NAME declaration was not found; this test would pin nothing").not.toBeNull();
    expect(found?.[1]).toBe(FULL_NAME.toString());
  });
});
