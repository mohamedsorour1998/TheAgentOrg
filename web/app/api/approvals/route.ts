/**
 * POST /api/approvals — THE ONE ROUTE THAT CAN OPEN A GATE OVER A NETWORK.
 *
 * =========================================================================
 * READ `web/lib/authz.ts` BEFORE CHANGING THIS FILE. Every refusal lives there,
 * as pure functions; this file is transport only. That split is deliberate: a
 * check that performs its own I/O can be bypassed by a caller that fetches
 * differently, so `decide()` takes already-measured facts and this route is the
 * only thing that measures them.
 * =========================================================================
 *
 * WHAT THIS REPLACES. `agentorg/approve_server.py` — no authentication, loopback
 * only, `by="ui-reviewer"` for every decision "because with no authentication the
 * server genuinely does not know who clicked". Lane K's control plane deliberately
 * has NO approval route at all, and its reasoning was right: "a scope nobody holds
 * reads as a capability that exists".
 *
 * So this route is the first one in the repository that can do this, and the whole
 * design is: **every refusal it can express is worth more than a route that accepts
 * and hopes.**
 *
 * THE ORDER OF OPERATIONS, AND WHY IT IS THIS ORDER
 * ================================================
 *   1. read the body (capped before the read)
 *   2. resolve the session  — server-side, never from the body
 *   3. measure the run      — its tenant, its status, its open gates
 *   4. measure the scope    — which repositories this tenant may act on
 *   5. `authz.decide(...)`  — one call, ten possible refusals
 *   6. only then: `queue.resume`
 *
 * Steps 2–4 are all MEASURED HERE and handed to step 5. A route that passed
 * anything from the body into `decide` would defeat it entirely, which is why
 * `ApprovalRequest` carries no `by` and no `tenant_id` and the contract says so.
 *
 * **NO GET.** `approve_server.do_GET` renders and never mutates, and its comment
 * says why that is what makes POST-only meaningful: `/decide` is reachable by a
 * back button, a bookmark or a prefetch and must be inert when it is. Next.js
 * answers 405 for an unexported method, so the absence IS the refusal here.
 */

import { NextResponse } from "next/server";

import { type ApprovalRequest } from "@/lib/endpoints";
import { decide } from "@/lib/authz";
import { readJson, refuse, respond, statusForRefusal, unhandled } from "@/lib/http";
import { allowedOrigins } from "@/lib/origins";
import { recordDecision, runFacts } from "@/lib/approvals";
import { currentIdentity, repositoriesInScope } from "@/lib/session";

/**
 * Pull the three fields out of an unknown body WITHOUT coercing them.
 *
 * Every field stays a string and is validated against an exact list inside
 * `decide`. Coercing here — `String(body.decision)` — would turn `null` into the
 * string `"null"` and an array into `"approved,rejected"`, so an unrecognised shape
 * would arrive at the validator looking like a word. A non-string is refused as a
 * SHAPE error before the vocabulary is consulted.
 */
function fieldsFrom(
  body: unknown,
): { ok: true; value: ApprovalRequest & { reason: string } } | { ok: false } {
  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    return { ok: false };
  }
  const record = body as Record<string, unknown>;
  const runId = record.run_id;
  const gate = record.gate;
  const decision = record.decision;
  const reason = record.reason ?? "";
  if (
    typeof runId !== "string" ||
    typeof gate !== "string" ||
    typeof decision !== "string" ||
    typeof reason !== "string"
  ) {
    return { ok: false };
  }
  // The cast is safe only because `decide` re-validates `gate` and `decision`
  // against exact lists. It is NOT a claim that they are valid.
  return {
    ok: true,
    value: { run_id: runId, gate, decision, reason } as ApprovalRequest & {
      reason: string;
    },
  };
}

export async function POST(request: Request): Promise<NextResponse> {
  try {
    const body = await readJson(request);
    if (!body.ok) {
      return body.response;
    }

    const fields = fieldsFrom(body.value);
    if (!fields.ok) {
      // The offending value is NOT echoed. See `web/lib/http.ts`.
      return refuse(
        "an approval needs run_id, gate and decision, each a string",
        400,
      );
    }

    // FROM THE SESSION. The only source of an identity in this file.
    const session = await currentIdentity();

    // MEASURED, not read off the request. `null` when the run does not exist OR
    // belongs to another tenant — `runFacts` collapses those deliberately, and
    // `decide` answers both with the same refusal.
    const facts =
      session === null ? null : await runFacts(session.tenantId, fields.value.run_id);

    const inScope =
      session === null ? [] : await repositoriesInScope(session.tenantId);

    const verdict = decide(
      {
        runId: fields.value.run_id,
        gate: fields.value.gate,
        decision: fields.value.decision,
        reason: fields.value.reason,
      },
      session,
      request.headers.get("origin"),
      facts,
      inScope,
      allowedOrigins(),
    );

    if (!verdict.permitted) {
      // AUDITED EVEN THOUGH IT WAS REFUSED. I8 asks for exactly this: "an
      // unauthorised approval attempt is refused AND RECORDED". A refusal nobody
      // records is a refusal nobody can count, and repeated attempts against one
      // run are the signal worth having.
      //
      // The refusal CODE is logged, never the body: the run id may be hostile and
      // this reaches a log an operator reads.
      console.warn(
        "[approvals] refused: code=%s login=%s",
        verdict.code,
        session?.login ?? "(anonymous)",
      );
      return refuse(verdict.message, statusForRefusal(verdict.code));
    }

    const recorded = await recordDecision(verdict);
    return respond(recorded);
  } catch (error) {
    return unhandled(error);
  }
}
