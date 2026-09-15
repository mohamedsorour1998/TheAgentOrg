/**
 * The TypeScript key layout must agree with the Python one, byte for byte.
 *
 * `web/lib/dynamo/keys.ts` is a SECOND DECLARATION of `agentorg/db/_dynamo.py`, and
 * that is deliberate rather than sloppy: the two run in different languages in
 * different processes and neither can import the other, so there is no shared source
 * to take it from. This repository's standing rule is that a second declaration must
 * be DETECTABLE, not avoided at all costs — the same exception
 * `tests/test_scoring_determinism.py` makes for `SEVERITY_ORDER`, and for the same
 * reason: two copies keep agreeing while one moves.
 *
 * **WHAT DRIFT WOULD DO.** The Python writer indexes a run at `TENANT#<id>` /
 * `RUN#<run_id>`; the TypeScript reader queries whatever it believes those are. A
 * rename on either side produces a reader that queries a partition nothing writes —
 * which returns ZERO ROWS, exits 0, and renders as "this tenant has no runs". There
 * is no error, no log line and no failing gate. That is the exact shape this
 * repository has now paid for twice, most recently with `TENANT_DB`.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  SEP,
  SK_BUDGET,
  SK_JOB,
  SK_MEMBER,
  SK_ORG,
  SK_PROFILE,
  SK_REPO,
  SK_RUN,
  SK_SECRET,
  TENANT_PREFIX,
  TENANT_ZERO_ID,
  USER_PREFIX,
  isSafeRunId,
  sk,
  tenantForRunState,
  tenantPk,
} from "../lib/dynamo/keys";

const REPO_ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..", "..");
const PY = readFileSync(path.join(REPO_ROOT, "agentorg", "db", "_dynamo.py"), "utf8");

/** Read a module-level `NAME = "value"` out of the Python source. */
function pyConst(name: string): string | null {
  const m = PY.match(new RegExp(`^${name}\\s*=\\s*"([^"]*)"`, "m"));
  return m?.[1] ?? null;
}

describe("the key layout agrees across the two languages", () => {
  it("reads the Python module at all", () => {
    // Anti-vacuity. Every assertion below compares against a regex match, and a
    // file that failed to load yields `null` for every one of them — which would
    // make the whole suite pass by matching nothing.
    expect(PY.length).toBeGreaterThan(500);
    expect(pyConst("SEP")).not.toBeNull();
  });

  it.each([
    ["SEP", SEP],
    ["TENANT_PREFIX", TENANT_PREFIX],
    ["USER_PREFIX", USER_PREFIX],
    ["SK_ORG", SK_ORG],
    ["SK_BUDGET", SK_BUDGET],
    ["SK_PROFILE", SK_PROFILE],
    ["SK_MEMBER", SK_MEMBER],
    ["SK_REPO", SK_REPO],
    ["SK_RUN", SK_RUN],
    ["SK_SECRET", SK_SECRET],
    ["SK_JOB", SK_JOB],
  ])("%s matches agentorg/db/_dynamo.py", (name, ours) => {
    const theirs = pyConst(name);
    expect(
      theirs,
      `${name} is not declared in agentorg/db/_dynamo.py; if it was renamed, this ` +
        `reader now queries a key nothing writes and the screen reads as "no runs"`,
    ).not.toBeNull();
    expect(theirs).toBe(ours);
  });

  it("builds the partition key the IAM policy authorises", () => {
    // `infra/Terraform/modules/tenancy/iam.tf` authorises
    // `TENANT#${aws:PrincipalTag/tenant}`. A different shape here is refused by AWS.
    expect(tenantPk("tenant-zero")).toBe("TENANT#tenant-zero");
  });

  it("refuses a blank tenant rather than building `TENANT#`", () => {
    // `TENANT#` is a REAL partition — the one an untagged session is authorised
    // for — so building it for a blank id would let an unscoped caller and an
    // unnamed tenant share rows.
    expect(() => tenantPk("")).toThrow();
  });

  it("translates the single-tenant marker exactly once", () => {
    expect(tenantForRunState("")).toBe(TENANT_ZERO_ID);
    // A real tenant is answered UNCHANGED. Rewriting it would silently reassign a
    // multi-tenant run, which looks identical in the data.
    expect(tenantForRunState("acme")).toBe("acme");
  });

  it("agrees with Python on which sort keys are singletons", () => {
    // A bare token for a non-singleton collapses every row of that type onto one
    // key, so the last write wins and the rest vanish with nothing raised.
    expect(sk(SK_ORG)).toBe("ORG");
    expect(sk(SK_RUN, "r1")).toBe("RUN#r1");
    expect(() => sk(SK_RUN)).toThrow();
  });

  it("refuses a run id that could traverse a path", () => {
    expect(isSafeRunId("95685400-b9b6-4aa6-bdaf-961fdfbbc744")).toBe(true);
    expect(isSafeRunId("../../etc/passwd")).toBe(false);
    expect(isSafeRunId("")).toBe(false);
    expect(isSafeRunId("..")).toBe(false);
  });
});
