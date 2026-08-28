/**
 * THE STATUS CODES, AND THE BODY CAP. What the HTTP layer refuses.
 *
 * The two properties worth a test here are both ones a reader would accept as
 * correct while they were wrong:
 *
 *   * a cross-tenant refusal must answer 404, not 403, or the code confirms that
 *     somebody else's run exists — undoing the collapse `authz.decide` performs;
 *   * the override must answer 422, not 403, or it reads as "you personally may
 *     not", which invites granting a permission that does not exist.
 */

import { describe, expect, it } from "vitest";

import { type RefusalCode } from "../authz";
import { readJson, statusForRefusal } from "../http";

/** Every refusal code, so the table below cannot silently omit one. */
const ALL_CODES: RefusalCode[] = [
  "cross-site-origin",
  "not-authenticated",
  "no-tenant",
  "unknown-gate",
  "unknown-decision",
  "override-not-permitted-here",
  "wrong-tenant",
  "repository-not-in-scope",
  "run-already-ended",
  "gate-not-awaiting",
];

describe("the refusal status table", () => {
  it("maps every refusal code to a client-or-conflict status", () => {
    // GUARD FIRST: if this list were empty the loop below would assert nothing and
    // pass, which is the vacuous-matcher shape this repository keeps finding.
    expect(ALL_CODES.length).toBe(10);
    for (const code of ALL_CODES) {
      const status = statusForRefusal(code);
      // Never a 200 and never a 500. A refusal answered 200 is the defect this
      // whole project exists to prevent; a refusal answered 500 reads as our bug
      // rather than as the caller's, so it would be retried forever.
      expect(status, code).toBeGreaterThanOrEqual(400);
      expect(status, code).toBeLessThan(500);
    }
  });

  it("answers a CROSS-TENANT refusal 404, never 403", () => {
    // 403 would confirm the run exists. `authz.decide` collapses "absent" and
    // "another tenant's" into one refusal and its tests assert the two answers are
    // byte-identical — this is what stops the HTTP layer re-separating them.
    expect(statusForRefusal("wrong-tenant")).toBe(404);
    expect(statusForRefusal("wrong-tenant")).not.toBe(403);
  });

  it("answers the OVERRIDE 422, never 403", () => {
    expect(statusForRefusal("override-not-permitted-here")).toBe(422);
    expect(statusForRefusal("override-not-permitted-here")).not.toBe(403);
  });

  it("answers an unauthenticated caller 401 and a scopeless one 403", () => {
    // Different codes because signing in again fixes one and not the other. A 401
    // for "you belong to no organisation" would send a person round the sign-in
    // loop forever.
    expect(statusForRefusal("not-authenticated")).toBe(401);
    expect(statusForRefusal("no-tenant")).toBe(403);
  });

  it("answers a decision on an ended run 409, not 400", () => {
    // The request was well formed; the world moved. 400 would read as a client
    // mistake and hide that the run really did end.
    expect(statusForRefusal("run-already-ended")).toBe(409);
    expect(statusForRefusal("gate-not-awaiting")).toBe(409);
  });
});

/** A request carrying a body, without needing a server. */
function post(body: string, headers: Record<string, string> = {}): Request {
  return new Request("https://app.example/api/approvals", {
    method: "POST",
    body,
    headers,
  });
}

describe("reading a body", () => {
  it("parses a valid JSON object", async () => {
    const result = await readJson(post('{"gate":"gate2"}'));
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value).toEqual({ gate: "gate2" });
    }
  });

  it("refuses an EMPTY body rather than reading it as {}", async () => {
    // An empty object would make "the caller sent nothing" indistinguishable from
    // "the caller sent {}", and the next thing the route does is decide a gate.
    // `agent_client` refuses a zero-byte body for exactly this reason.
    const result = await readJson(post(""));
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.status).toBe(400);
    }
  });

  it("refuses a body that is not JSON", async () => {
    const result = await readJson(post("gate=gate2&decision=approved"));
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.status).toBe(400);
    }
  });

  it("does NOT echo the offending body in the refusal", async () => {
    // Attacker-controlled text on a page a human reads. `approve_server._one`
    // refuses to echo for the same reason.
    const hostile = "<script>alert(1)</script>";
    const result = await readJson(post(hostile));
    expect(result.ok).toBe(false);
    if (!result.ok) {
      const text = await result.response.text();
      expect(text).not.toContain("script");
      expect(text).not.toContain(hostile);
    }
  });

  it("refuses a body over the cap on its DECLARED length, before reading it", async () => {
    // The header check is what saves the allocation. Asserted with a small body and
    // a large declared length, so a passing result can only come from the header
    // path — the body itself is well under the cap.
    const result = await readJson(
      post('{"gate":"gate2"}', { "content-length": String(10 * 1024 * 1024) }),
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.status).toBe(413);
    }
  });

  it("refuses an over-cap body that declared NO length", async () => {
    // A chunked request carries no Content-Length at all, so the header check
    // cannot fire and the post-read check is the one that holds. Without it the cap
    // would be advisory — honoured only by callers that announce themselves.
    const huge = JSON.stringify({ reason: "x".repeat(70 * 1024) });
    const request = new Request("https://app.example/api/approvals", {
      method: "POST",
      body: huge,
    });
    request.headers.delete("content-length");
    const result = await readJson(request);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.status).toBe(413);
    }
  });

  it("refuses an unreadable declared length", async () => {
    const result = await readJson(
      post('{"gate":"gate2"}', { "content-length": "not-a-number" }),
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.status).toBe(400);
    }
  });
});
