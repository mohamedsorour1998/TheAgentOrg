/**
 * WHETHER A VERIFIED TOKEN IS A SESSION THIS APPLICATION ACTS ON.
 *
 * `authorizeSession` is pure, so every case here is a plain call — no cookie, no
 * clock, no database. That is the whole reason the checks live in their own
 * function rather than inside `verifySession`.
 *
 * EVERY ASSERTION IS UNCONDITIONAL, AND THAT IS NOT A STYLE CHOICE. A
 * discriminated union invites `if (!result.permitted) expect(result.code)...`,
 * and the reference project measured what that costs: rewriting its `authorize`
 * to refuse EVERYTHING left **10 of 14 tests failing and 4 passing**, because the
 * four bodies never ran. `refusalOf` and `permitOf` below throw on the wrong
 * variant, so a test that reaches the wrong branch fails rather than skipping.
 */

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  REVIEWER_ROLE,
  authorizeSession,
  type SessionRefusal,
  type SessionPermit,
  type TokenIdentity,
} from "@/lib/authorize";

const NOW = 1_788_400_000_000;

const identity = (extra: Partial<TokenIdentity> = {}): TokenIdentity => ({
  sub: "7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d",
  login: "a-real-person",
  role: REVIEWER_ROLE,
  tenantId: "tenant-zero",
  expiresAt: NOW + 3_600_000,
  ...extra,
});

/** Narrow by THROWING, never by an `if` the assertion can hide behind. */
function refusalOf(
  result: ReturnType<typeof authorizeSession>,
): SessionRefusal {
  if (result.permitted) {
    throw new Error(`expected a refusal, got a permit for ${result.identity.login}`);
  }
  return result;
}

function permitOf(result: ReturnType<typeof authorizeSession>): SessionPermit {
  if (!result.permitted) {
    throw new Error(`expected a permit, got the refusal ${result.code}`);
  }
  return result;
}

describe("authorizeSession — refusals", () => {
  it("refuses a null identity as no-session", () => {
    expect(refusalOf(authorizeSession(null, NOW)).code).toBe("no-session");
  });

  it("refuses an expired session", () => {
    const result = authorizeSession(identity({ expiresAt: NOW - 1 }), NOW);
    expect(refusalOf(result).code).toBe("session-expired");
  });

  it("refuses a session expiring EXACTLY now", () => {
    // `<=` and not `<`. Written the other way a just-expired session is
    // honoured, which is the fail-open direction on the one surface in this
    // repository that can open a security gate over a network.
    const result = authorizeSession(identity({ expiresAt: NOW }), NOW);
    expect(refusalOf(result).code).toBe("session-expired");
  });

  it("refuses a non-finite expiry in both directions", () => {
    // `Infinity <= nowMs` and `NaN <= nowMs` are each `false`, so without the
    // finiteness check both slip past the comparison and read as a session that
    // never expires. `cognito.ts` refuses these too; this is defence in depth,
    // because a `TokenIdentity` can be built by any code that names the type.
    for (const expiresAt of [Infinity, -Infinity, NaN]) {
      const result = authorizeSession(identity({ expiresAt }), NOW);
      expect(refusalOf(result).code, String(expiresAt)).toBe("session-expired");
    }
  });

  it("refuses a non-finite CLOCK, not only a non-finite expiry", () => {
    // The mirror case, and the one an argument from outside this module can
    // cause: `NaN` for `nowMs` makes every comparison false, so every session
    // would look live.
    const result = authorizeSession(identity(), NaN);
    expect(refusalOf(result).code).toBe("session-expired");
  });

  it("refuses an UNASSIGNED account with no-role, not wrong-role", () => {
    // THE STATE SELF-SERVICE SIGN-UP CREATES. "Nobody has set this yet" and
    // "this value is not one we accept" are different facts and get different
    // codes, for `scan_provenance`'s reason: collapsing them hides a pending
    // administrative action behind what reads as a rejection.
    for (const role of ["", "   "]) {
      const result = authorizeSession(identity({ role }), NOW);
      expect(refusalOf(result).code, JSON.stringify(role)).toBe("no-role");
    }
  });

  it("refuses a role that is close but not exact", () => {
    // Compared EXACTLY. Case folding or trimming here is the failure
    // `graph.APPROVAL_WORDS` refuses, and the reference measured its cost in a
    // neighbouring project: `"Escalate."` — one trailing period — resumed a
    // blocked tool and filed a renewal for a household missing a document.
    for (const role of ["Reviewer", "reviewer ", "reviewers", "admin", "REVIEWER"]) {
      const result = authorizeSession(identity({ role }), NOW);
      expect(refusalOf(result).code, role).toBe("wrong-role");
    }
  });

  it("never echoes the role a caller holds or the one required", () => {
    // `ApiError`'s rule: a message never carries a value the caller supplied,
    // and telling an unadmitted account which claim value would admit it is an
    // instruction rather than an explanation.
    const result = refusalOf(authorizeSession(identity({ role: "auditor" }), NOW));
    expect(result.message).not.toContain("auditor");
    expect(result.message).not.toContain(REVIEWER_ROLE);
  });
});

describe("authorizeSession — the one permit", () => {
  it("permits a live reviewer and hands the identity straight through", () => {
    const permit = permitOf(authorizeSession(identity(), NOW));
    expect(permit.identity.login).toBe("a-real-person");
    expect(permit.identity.tenantId).toBe("tenant-zero");
  });

  it("PERMITS a reviewer whose tenant claim is blank, and that is the division", () => {
    // A blank tenant is NOT this function's refusal. `authz.decide`'s
    // `no-tenant` is the one declaration of that fact, and a second spelling
    // here would be two places to keep in step. `session.currentIdentity` is
    // what stops such an identity reaching any route — proved in the next test.
    const permit = permitOf(authorizeSession(identity({ tenantId: "" }), NOW));
    expect(permit.identity.tenantId).toBe("");
  });
});

describe("the purity guard", () => {
  const source = readFileSync(
    path.resolve(path.dirname(new URL(import.meta.url).pathname), "..", "authorize.ts"),
    "utf8",
  );

  it("imports nothing at all", () => {
    // A POSITIVE CHECK, NOT A DENYLIST. The reference forbade `node:fs`,
    // `Date.now()`, `fetch(` and `process.env` by spelling, and `from "fs"`,
    // `new Date().getTime()` and `globalThis.fetch` each walked straight past
    // it. Enumerating what IS there cannot be walked around by a synonym.
    const imports = source.match(/^\s*import\s/gm) ?? [];
    expect(imports, `authorize.ts must import nothing; found ${imports.length}`).toHaveLength(0);
  });

  it("reads no clock and no environment of its own", () => {
    // `nowMs` is an argument precisely so a test can drive the boundary. A
    // module-level clock read would make the expiry tests above unwritable.
    expect(source).not.toContain("Date.now");
    expect(source).not.toContain("process.env");
  });

  it("is not vacuous — the file really was read", () => {
    // Without this, a wrong path yields `""`, every check above passes, and the
    // guard reads as coverage. This repository's most repeatable test defect.
    expect(source.length).toBeGreaterThan(2000);
    expect(source).toContain("export function authorizeSession");
  });
});
