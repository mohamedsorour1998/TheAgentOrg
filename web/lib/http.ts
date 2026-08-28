/**
 * THE ONE SHAPE EVERY ROUTE ANSWERS WITH, and the session it resolves first.
 *
 * Every route in `web/app/api/**` goes through `respond` / `refuse` here, so a
 * caller integrating against one endpoint does not learn a second vocabulary — the
 * reason `agentorg/api/__init__.py` reuses `agents/server.py`'s codes verbatim.
 *
 * THE STATUS CODES, AND THE TWO CHOICES THAT ARE NOT OBVIOUS
 * =========================================================
 *   400  the body is not JSON, or a field is the wrong shape
 *   401  nobody is signed in
 *   403  signed in, but not permitted — used ONLY where the resource is already
 *        known to the caller
 *   404  no such thing, AND every cross-tenant read
 *   409  a conflicting state — a decision on a run that has already ended
 *   422  a valid body naming something this surface will not do
 *   500  an unhandled failure, with its type
 *
 * **404 FOR A CROSS-TENANT RUN, NOT 403.** Lane K makes the split per-resource and
 * this layer inherits it rather than inventing a second convention: a run id is an
 * unguessable uuid, so answering 403 would confirm that somebody else's run exists.
 * `authz.decide` already collapses "absent" and "another tenant's" into one
 * refusal, and its tests assert the two answers are byte-identical; this table is
 * what keeps the HTTP layer from re-separating them.
 *
 * **422 FOR THE OVERRIDE, NOT 403.** `decision: "overridden"` is a real word this
 * surface refuses on policy. 403 would read as "you personally may not", inviting
 * somebody to grant a permission that does not exist; 422 says the request is
 * understood and will not be actioned here, and the message names the shell route
 * that does permit it.
 *
 * NOTHING HERE EVER ECHOES A CALLER-SUPPLIED VALUE. `approve_server._one` refuses
 * to for the same reason: it is attacker-controlled text on a page a human reads,
 * and the human does not need it to fix a mis-clicked form.
 */

import { NextResponse } from "next/server";

import type { ApiError } from "./endpoints";
import { type RefusalCode } from "./authz";

/** A successful answer. */
export function respond<T>(body: T, status = 200): NextResponse {
  return NextResponse.json(body, {
    status,
    headers: {
      // NEVER CACHED. Every one of these responses is per-session and per-tenant,
      // and a shared cache holding one would serve one tenant's run list to
      // another — the single worst thing this layer could do, achieved by
      // omission rather than by a bug.
      "Cache-Control": "no-store, private",
    },
  });
}

/** A refusal, in the one error shape. */
export function refuse(
  message: string,
  status: number,
  detail?: string,
): NextResponse {
  const body: ApiError = detail ? { error: message, detail } : { error: message };
  return NextResponse.json(body, {
    status,
    headers: { "Cache-Control": "no-store, private" },
  });
}

/**
 * The HTTP status for each of `authz.decide`'s refusal codes.
 *
 * A TOTAL MAP, not a lookup with a default. `Record<RefusalCode, number>` makes a
 * new refusal code a TYPE ERROR here rather than a silent 400 — which matters
 * because the default would be the most permissive-looking answer, and a reviewer
 * adding a code would have no signal that its status was never chosen.
 */
const REFUSAL_STATUS: Record<RefusalCode, number> = {
  // Not 401: the caller may well be signed in. This is about where the request
  // came FROM, and `approve_server` answers its cross-site refusal the same way.
  "cross-site-origin": 403,
  "not-authenticated": 401,
  // Signed in, but attached to no organisation. 403 rather than 401 — signing in
  // again will not help, which is what a 401 would imply.
  "no-tenant": 403,
  "unknown-gate": 400,
  "unknown-decision": 400,
  "override-not-permitted-here": 422,
  // THE CROSS-TENANT ANSWER. 404, deliberately. See this module's header.
  "wrong-tenant": 404,
  "repository-not-in-scope": 403,
  // A conflicting state rather than a bad request: the run really did end.
  "run-already-ended": 409,
  "gate-not-awaiting": 409,
};

export function statusForRefusal(code: RefusalCode): number {
  return REFUSAL_STATUS[code];
}

/**
 * Read and parse a JSON body, with the size cap applied BEFORE the read.
 *
 * `agents/server.py` checks its 4 MiB cap before reading "so a hostile length
 * cannot make the container allocate", and the same reasoning applies here: a
 * request declaring 500 MB must be refused on its header, not after Node has
 * buffered it. This surface's bodies are a run id, a gate and a decision, so the
 * cap is small on purpose — 64 KiB, the same as `approve_server._MAX_BODY`.
 *
 * AN EMPTY BODY IS NOT PARSED AS `{}`. Returning an empty object would make "the
 * caller sent nothing" indistinguishable from "the caller sent an empty object",
 * and the next thing a route does with the result is decide a gate.
 */
const MAX_BODY_BYTES = 64 * 1024;

export async function readJson(
  request: Request,
): Promise<{ ok: true; value: unknown } | { ok: false; response: NextResponse }> {
  const declared = request.headers.get("content-length");
  if (declared !== null) {
    const length = Number(declared);
    if (!Number.isFinite(length) || length < 0) {
      return {
        ok: false,
        response: refuse("the request declared an unreadable length", 400),
      };
    }
    if (length > MAX_BODY_BYTES) {
      return {
        ok: false,
        response: refuse(
          "the request body is larger than this endpoint accepts",
          413,
          `the cap is ${MAX_BODY_BYTES} bytes`,
        ),
      };
    }
  }

  let text: string;
  try {
    text = await request.text();
  } catch {
    return { ok: false, response: refuse("the request body could not be read", 400) };
  }

  // Checked again after the read: `Content-Length` is a claim, and a chunked
  // request carries none at all. The header check above saves the allocation when
  // the claim is honest; this one is what actually holds.
  if (text.length > MAX_BODY_BYTES) {
    return {
      ok: false,
      response: refuse(
        "the request body is larger than this endpoint accepts",
        413,
        `the cap is ${MAX_BODY_BYTES} bytes`,
      ),
    };
  }

  if (text.trim() === "") {
    return { ok: false, response: refuse("the request body was empty", 400) };
  }

  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    // The parse error is NOT included: it quotes the offending input, which is
    // caller-supplied text this layer does not echo.
    return { ok: false, response: refuse("the request body is not valid JSON", 400) };
  }
}

/**
 * Turn an unexpected failure into a 500 that NAMES it.
 *
 * `agents/server.py` carries the exception type on its 500 for the reason quoted
 * throughout this repository: "a green response meaning 'the check did not run' is
 * the one answer this pipeline must never accept." A generic 500 with no type is
 * one step from that.
 *
 * The MESSAGE is included and the STACK is not. A stack names filesystem paths and
 * internals, and `approve_server.do_POST` sends its detail to the server log rather
 * than to the client for exactly that reason.
 */
export function unhandled(error: unknown): NextResponse {
  const name = error instanceof Error ? error.name : typeof error;
  const message = error instanceof Error ? error.message : String(error);
  // Logged in full where an operator reads it; sent narrow.
  console.error("[api] unhandled failure", error);
  return refuse("that request could not be processed", 500, `${name}: ${message}`);
}
