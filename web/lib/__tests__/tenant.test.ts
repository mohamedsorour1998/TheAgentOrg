/**
 * THE TENANT CLAIM'S SHAPE, and the refusals it is deliberately NOT making.
 *
 * `tenantFromClaim` is not an authorisation check — `authz.decide`'s `no-tenant`
 * is the one declaration of that. It answers a narrower question: is this string
 * the shape of a tenant id this deployment could have issued? A malformed value
 * refused here fails where it can be named, rather than several layers away
 * inside a Python context manager as a `ValueError` about a blank scope.
 *
 * The tests that assert what it ALLOWS matter as much as the ones asserting what
 * it refuses. A stricter pattern would be this application inventing a constraint
 * `agentorg/db/engine.py` does not share, and the symptom would be a
 * legitimately-issued tenant producing a null session with nothing saying which
 * of the two halves refused it.
 */

import { describe, expect, it } from "vitest";

import { MAX_TENANT_ID_LENGTH, tenantFromClaim } from "@/lib/tenant";

describe("tenantFromClaim — refusals", () => {
  it("refuses absent, blank and whitespace-only claims", () => {
    // `engine.acting_as` refuses a blank too — "a blank scope matches a blank
    // column and that is a row nobody owns" — so this is the same refusal made
    // where a reader can see which value caused it.
    for (const value of [undefined, null, "", "   ", "\t\n"]) {
      expect(tenantFromClaim(value), JSON.stringify(value)).toBeNull();
    }
  });

  it("refuses a claim carrying a control character", () => {
    // A tenant id travels as a JSON argument to a Python subprocess and lands in
    // a log line. A newline in that value can forge a log row, and no legitimate
    // identifier contains one. `\u007f` is DEL, which is NOT below 0x20 and so
    // needs its own arm of the check — a `< 0x20` test alone would admit it.
    for (const bad of [
      "tenant\nzero",
      "tenant\tzero",
      "tenant\u0000zero",
      "tenant\u007fzero",
      "\u001btenant",
    ]) {
      expect(tenantFromClaim(bad), JSON.stringify(bad)).toBeNull();
    }
  });

  it("refuses a claim longer than the cap and accepts one exactly at it", () => {
    // BOTH SIDES OF THE BOUNDARY. A test that only tried an over-long value
    // would pass against an off-by-one that refused every legitimate id.
    expect(tenantFromClaim("t".repeat(MAX_TENANT_ID_LENGTH + 1))).toBeNull();
    expect(tenantFromClaim("t".repeat(MAX_TENANT_ID_LENGTH))).toBe(
      "t".repeat(MAX_TENANT_ID_LENGTH),
    );
  });
});

describe("tenantFromClaim — what it returns, and what it deliberately allows", () => {
  it("returns the trimmed value for the tenant this repository actually issues", () => {
    expect(tenantFromClaim("tenant-zero")).toBe("tenant-zero");
    expect(tenantFromClaim("  tenant-zero  ")).toBe("tenant-zero");
  });

  it("ALLOWS interior spaces and unusual punctuation, on purpose", () => {
    // `agentorg/db/engine.py:81` imposes exactly one rule — not blank. A
    // stricter pattern here would refuse a tenant the database half accepts,
    // and the failure would be a null session with nothing naming the cause.
    // If this ever becomes wrong, the fix is a shared constraint rather than a
    // second one here.
    for (const value of ["acme corp", "org/team", "t_1.2+3", "客户-1"]) {
      expect(tenantFromClaim(value), value).toBe(value);
    }
  });

  it("accepts a uuid, which is the other shape a pool is likely to carry", () => {
    const uuid = "7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d";
    expect(tenantFromClaim(uuid)).toBe(uuid);
  });
});

describe("what tenant.ts no longer does", () => {
  it("exports no membership query, because nothing reads the table for a tenant", async () => {
    // THE CIRCULARITY IS GONE RATHER THAN WORKED AROUND. `membershipsFor` and
    // `soleTenant` read the RLS-scoped `membership` table to discover the tenant
    // RLS needed bound; the claim replaces them. Keeping them exported with no
    // caller would be this repository's second named pattern — a correct answer
    // nobody asks for — so their ABSENCE is asserted rather than assumed.
    // Named `exported` and not `module`: `@next/next/no-assign-module-variable`
    // refuses the latter, because in a CommonJS scope `module` is the real thing
    // and shadowing it breaks the bundle rather than the test.
    const exported: Record<string, unknown> = await import("@/lib/tenant");
    expect(Object.keys(exported).sort()).toEqual([
      "MAX_TENANT_ID_LENGTH",
      "tenantFromClaim",
    ]);
  });
});
