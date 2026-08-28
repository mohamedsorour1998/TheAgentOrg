/**
 * WHAT THE APPROVAL SURFACE REFUSES. The most important tests in this lane.
 *
 * Mostly refusals, in the spirit of `tests/test_approve_server.py`, which is 95
 * tests of "mostly what it refuses". Every test here names the exact hole it
 * closes, because a test whose subject is not obvious from its name is one nobody
 * will maintain when the mechanism moves.
 *
 * THE STRUCTURAL GUARD FIRST. Several tests below build an attempt that should be
 * permitted and then break one thing. If the permitted baseline ever stops being
 * permitted -- a new required check, a renamed field -- every one of those tests
 * would keep passing while testing nothing at all, because they assert on a
 * refusal and would get one for the wrong reason. So `test the baseline IS
 * permitted` runs first and the others assert on the refusal CODE, never merely
 * on `permitted === false`.
 */

import { describe, expect, it } from "vitest";

import {
  type ApprovalAttempt,
  type RunFacts,
  type SessionIdentity,
  decide,
  originIsAcceptable,
} from "../authz";

const ORIGINS = ["https://app.example"] as const;
const IN_SCOPE = ["acme/auth-service"] as const;

const SESSION: SessionIdentity = {
  login: "a-real-person",
  tenantId: "tenant-acme",
};

const FACTS: RunFacts = {
  runId: "11111111-2222-3333-4444-555555555555",
  tenantId: "tenant-acme",
  repositoryFullName: "acme/auth-service",
  status: "running",
  awaitingGates: ["gate2"],
};

const ATTEMPT: ApprovalAttempt = {
  runId: FACTS.runId,
  gate: "gate2",
  decision: "approved",
  reason: "the scanners passed and I read the diff",
};

/** The permitted call, so each test below breaks exactly one thing. */
function ask(
  overrides: {
    attempt?: Partial<ApprovalAttempt>;
    session?: SessionIdentity | null;
    origin?: string | null;
    facts?: Partial<RunFacts> | null;
    inScope?: readonly string[];
  } = {},
) {
  const session =
    overrides.session === undefined ? SESSION : overrides.session;
  const facts =
    overrides.facts === null ? null : { ...FACTS, ...(overrides.facts ?? {}) };
  return decide(
    { ...ATTEMPT, ...(overrides.attempt ?? {}) },
    session,
    overrides.origin === undefined ? "https://app.example" : overrides.origin,
    facts,
    overrides.inScope ?? IN_SCOPE,
    ORIGINS,
  );
}

describe("the baseline, so every refusal below is for the reason it names", () => {
  it("permits a signed-in member deciding an open gate on an in-scope repository", () => {
    const answer = ask();
    // Asserted with the code visible, so a change to the permit shape fails here
    // rather than silently making eleven refusal tests vacuous.
    expect(answer).toEqual({
      permitted: true,
      runId: FACTS.runId,
      gate: "gate2",
      decision: "approved",
      reason: ATTEMPT.reason,
      by: "a-real-person",
      tenantId: "tenant-acme",
    });
  });

  it("attributes the decision to the SESSION, never to anything in the body", () => {
    // THE WHOLE DIFFERENCE FROM approve_server, which records "ui-reviewer" for
    // every decision because with no auth it does not know who clicked. A body
    // field named `by` must not reach the record even if a client sends one.
    const hostile = {
      ...ATTEMPT,
      by: "somebody-else",
      login: "somebody-else",
    } as ApprovalAttempt;
    const answer = decide(hostile, SESSION, null, FACTS, IN_SCOPE, ORIGINS);
    expect(answer.permitted).toBe(true);
    if (answer.permitted) {
      expect(answer.by).toBe("a-real-person");
    }
  });
});

describe("authentication", () => {
  it("refuses an unauthenticated caller", () => {
    const answer = ask({ session: null });
    expect(answer).toMatchObject({
      permitted: false,
      code: "not-authenticated",
    });
  });

  it("refuses a session attached to no organisation", () => {
    // A blank tenant is not a scope in its own right -- `engine.acting_as`
    // refuses one, because "a blank scope matches a blank column and that is a
    // row nobody owns".
    const answer = ask({ session: { login: "nobody", tenantId: "" } });
    expect(answer).toMatchObject({ permitted: false, code: "no-tenant" });
  });

  it("refuses a whitespace-only tenant, not only an empty one", () => {
    const answer = ask({ session: { login: "nobody", tenantId: "   " } });
    expect(answer).toMatchObject({ permitted: false, code: "no-tenant" });
  });

  it("refuses BEFORE resolving a tenant, so an anonymous caller drives no read", () => {
    // The ordering `infra/ingress/handler.py` establishes: everything that costs
    // anything happens after the identity check. Passing `facts: null` proves the
    // refusal did not need them -- if the tenant check ran first this would
    // answer `wrong-tenant` instead.
    const answer = ask({ session: null, facts: null });
    expect(answer).toMatchObject({ code: "not-authenticated" });
  });
});

describe("the tenant boundary", () => {
  it("refuses a run belonging to another tenant", () => {
    const answer = ask({ facts: { tenantId: "tenant-someone-else" } });
    expect(answer).toMatchObject({ permitted: false, code: "wrong-tenant" });
  });

  it("answers a cross-tenant run and an absent run IDENTICALLY", () => {
    // Distinguishing them would itself be the disclosure: a run id is an
    // unguessable uuid, so the caller learns only what they already tried. Lane
    // B's leak suite records the same convention.
    const other = ask({ facts: { tenantId: "tenant-someone-else" } });
    const absent = ask({ facts: null });
    expect(other).toEqual(absent);
  });

  it("does not name the owning tenant in the refusal", () => {
    // `CrossTenantAccess`'s message carries only the identifier the CALLER
    // supplied, never a field from the other tenant's row.
    const answer = ask({ facts: { tenantId: "tenant-victim" } });
    expect(answer.permitted).toBe(false);
    if (!answer.permitted) {
      expect(answer.message).not.toContain("tenant-victim");
    }
  });
});

describe("per-repository authorisation", () => {
  it("refuses a run whose repository is not in the tenant's scope", () => {
    // Being in the tenant is not enough. A repository removed from scope must
    // not remain approvable.
    const answer = ask({ inScope: ["acme/something-else"] });
    expect(answer).toMatchObject({
      permitted: false,
      code: "repository-not-in-scope",
    });
  });

  it("refuses when the tenant has no repositories in scope at all", () => {
    // AN EMPTY SCOPE IS A REFUSAL, not an exemption -- the direction Lane K's
    // empty key store and `budgets.check` with no budget row both take.
    const answer = ask({ inScope: [] });
    expect(answer).toMatchObject({ code: "repository-not-in-scope" });
  });
});

describe("the vocabulary, exactly", () => {
  it("refuses a gate that is not one of the three", () => {
    const answer = ask({ attempt: { gate: "gate4" } });
    expect(answer).toMatchObject({ permitted: false, code: "unknown-gate" });
  });

  it("refuses a decision word it does not recognise", () => {
    const answer = ask({ attempt: { decision: "looks-fine" } });
    expect(answer).toMatchObject({ permitted: false, code: "unknown-decision" });
  });

  it.each(["APPROVED", "Approved", " approved", "approved "])(
    "refuses %j — no case folding and no trimming",
    (decision) => {
      // The fail-closed rule `approve_server._DECISIONS` and
      // `graph.APPROVAL_WORDS` both state: on the prompts where being misread is
      // most expensive, anything that is not an explicit exact decision must not
      // become one.
      const answer = ask({ attempt: { decision } });
      expect(answer).toMatchObject({ code: "unknown-decision" });
    },
  );

  it.each(["GATE2", "gate2 ", " gate2"])(
    "refuses gate %j — same exactness",
    (gate) => {
      const answer = ask({ attempt: { gate } });
      expect(answer).toMatchObject({ code: "unknown-gate" });
    },
  );

  it("refuses an empty decision and an empty gate", () => {
    expect(ask({ attempt: { decision: "" } })).toMatchObject({
      code: "unknown-decision",
    });
    expect(ask({ attempt: { gate: "" } })).toMatchObject({
      code: "unknown-gate",
    });
  });
});

describe("the override, refused with its own code", () => {
  it("refuses `overridden` from the web application", () => {
    // `approve_server.py` made the same trade and stated the cost. A network
    // endpoint is strictly weaker than a shell, so it must not widen what a
    // loopback-only screen already refused.
    const answer = ask({ attempt: { decision: "overridden" } });
    expect(answer).toMatchObject({
      permitted: false,
      code: "override-not-permitted-here",
    });
  });

  it("does NOT report the override as an unrecognised word", () => {
    // "you named something real that this surface will not do" and "you named
    // nothing I recognise" are different facts, and an operator reading the audit
    // needs to tell them apart. Folding them would make a deliberate policy read
    // as a typo.
    const answer = ask({ attempt: { decision: "overridden" } });
    expect(answer).not.toMatchObject({ code: "unknown-decision" });
  });

  it("names the shell route that DOES permit it", () => {
    // A refusal that does not say what to do instead sends the next person
    // looking for a broken route.
    const answer = ask({ attempt: { decision: "overridden" } });
    expect(answer.permitted).toBe(false);
    if (!answer.permitted) {
      expect(answer.message).toContain("gates_cli");
    }
  });
});

describe("THE gates.resume GAP, refused at the boundary", () => {
  it("refuses an approval on a run the graph already REJECTED", () => {
    // ─────────────────────────────────────────────────────────────────────────
    // THE HOLE THIS CLOSES. `gates.resume` sets status="rejected" for a rejection
    // and never un-sets it, so approving a rejected run leaves status="rejected"
    // while APPENDING the approval -- and `timeline.py` renders that approval
    // AFTER the rejection, so a rejected run displays a later approval on the
    // timeline the judges read.
    //
    // The queue row is still paused here, which is the point: the two records
    // disagree, and this refusal trusts the one that knows the run ended.
    // ─────────────────────────────────────────────────────────────────────────
    const answer = ask({
      facts: { status: "rejected", awaitingGates: ["gate2"] },
    });
    expect(answer).toMatchObject({
      permitted: false,
      code: "run-already-ended",
    });
  });

  it.each(["blocked", "promoted", "failed", "rejected"] as const)(
    "refuses a decision on a run that ended as %s",
    (status) => {
      // All four endings, not only the rejection. A blocked run is the poisoned
      // demo's whole point and must not be approvable afterwards.
      const answer = ask({ facts: { status, awaitingGates: ["gate2"] } });
      expect(answer).toMatchObject({ code: "run-already-ended" });
    },
  );

  it("checks the run's STATUS as well as the queue's paused row", () => {
    // If only `awaitingGates` were consulted, the case above would be PERMITTED
    // -- the queue row says paused. This asserts the status check is load-bearing
    // by showing the two inputs disagreeing and the refusal still happening.
    const stillPaused = ask({
      facts: { status: "rejected", awaitingGates: ["gate2"] },
    });
    const notPaused = ask({
      facts: { status: "running", awaitingGates: [] },
    });
    expect(stillPaused).toMatchObject({ code: "run-already-ended" });
    expect(notPaused).toMatchObject({ code: "gate-not-awaiting" });
  });
});

describe("the gate must be open right now", () => {
  it("refuses a gate this run is not awaiting", () => {
    const answer = ask({ facts: { awaitingGates: ["gate1"] } });
    expect(answer).toMatchObject({
      permitted: false,
      code: "gate-not-awaiting",
    });
  });

  it("refuses a second decision on a gate already decided", () => {
    // A decided gate leaves `awaitingGates`, so this is the same predicate --
    // asserted separately because it is a different mistake and would otherwise
    // rest on a reader knowing they collapse.
    const answer = ask({ facts: { awaitingGates: [] } });
    expect(answer).toMatchObject({ code: "gate-not-awaiting" });
  });
});

describe("cross-site origin", () => {
  it("refuses a POST carrying another site's Origin", () => {
    // Loopback binding does not stop a page in the operator's own browser posting
    // here, and this application is not loopback-bound at all.
    const answer = ask({ origin: "https://evil.example" });
    expect(answer).toMatchObject({
      permitted: false,
      code: "cross-site-origin",
    });
  });

  it("refuses the right host on the WRONG SCHEME", () => {
    // Matched on the exact origin string, not on hostname: a host-only match
    // would accept `http://` where only `https://` is served, which is a
    // downgrade an attacker chooses.
    const answer = ask({ origin: "http://app.example" });
    expect(answer).toMatchObject({ code: "cross-site-origin" });
  });

  it("refuses a host that merely CONTAINS the allowed one", () => {
    const answer = ask({ origin: "https://app.example.evil.test" });
    expect(answer).toMatchObject({ code: "cross-site-origin" });
  });

  it("refuses cross-site BEFORE authenticating, so a probe learns nothing", () => {
    const answer = ask({ origin: "https://evil.example", session: null });
    expect(answer).toMatchObject({ code: "cross-site-origin" });
  });

  it("allows an ABSENT Origin, which is not the threat this addresses", () => {
    // `approve_server._check_origin` allows it too: "curl and the CLI send no
    // Origin, and the documented fallback path must keep working." A browser
    // always sends it on a cross-site POST, so absent means not-a-browser.
    expect(ask({ origin: null }).permitted).toBe(true);
    expect(ask({ origin: "" }).permitted).toBe(true);
  });

  it("originIsAcceptable refuses everything when no origin is configured", () => {
    // An empty allow-list must refuse a present Origin rather than admit it. The
    // permissive reading -- "nothing configured, so allow all" -- is the same
    // fail-open shape as an empty key store granting access.
    expect(originIsAcceptable("https://app.example", [])).toBe(false);
    expect(originIsAcceptable(null, [])).toBe(true);
  });
});
