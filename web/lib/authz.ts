/**
 * WHO MAY OPEN A GATE. The security core of I5, as pure functions.
 *
 * =========================================================================
 * THIS IS THE FIRST SURFACE IN THIS REPOSITORY THAT CAN OPEN A GATE OVER A
 * NETWORK. `agentorg/approve_server.py` is loopback-only and unauthenticated;
 * Lane K's control plane deliberately has NO approval route at all.
 * =========================================================================
 *
 * `approve_server.py`'s docstring names three things standing in for the
 * authentication it lacks -- loopback binding, POST-only mutations, and a
 * cross-site `Origin` refusal -- and says plainly that none is a substitute. It
 * records `by="ui-reviewer"` for every decision because "with no authentication
 * the server genuinely does not know who clicked".
 *
 * This module is the authentication that did not exist. Everything here is a
 * REFUSAL; nothing here approves anything. `decide()` returns either a permit
 * carrying the identity to attribute, or a refusal carrying the sentence to show
 * and the reason to audit.
 *
 * WHY PURE FUNCTIONS OVER ALREADY-FETCHED FACTS
 * ============================================
 * No I/O, no session lookup, no database. Two reasons, and the second is the one
 * that matters:
 *
 *   * it is testable with no Postgres, no Python and no network, which is what
 *     keeps these checks inside the hermetic suite this repository gates on;
 *   * a check that performs its own I/O can be BYPASSED by a caller that fetches
 *     differently. With the facts as parameters, a route physically cannot call
 *     this with a tenant it did not resolve server-side.
 *
 * WHY ONE FUNCTION AND ONE REFUSAL TYPE
 * =====================================
 * `approve_server._Refused` makes the same choice for the same reason, quoted
 * because it is exactly right: "the failure that loses a security gate is not the
 * explicit reject; it is one branch out of several falling through to approval."
 * Every refusal below leaves through one `return`, and the permit is built at the
 * bottom of one function with every check above it.
 */

import type { Gate, RunStatus } from "./contract";

/**
 * Who is asking. Built ONLY from a verified server-side session.
 *
 * `login` is the GitHub login rather than an email or a database id, because it
 * is what a person recognises on a timeline and what the GitHub grant already
 * establishes. It becomes `HumanDecision.by`, so it is the whole difference
 * between this surface and `approve_server`'s constant.
 */
export interface SessionIdentity {
  login: string;
  tenantId: string;
}

/** What the server measured about the run. Never supplied by the client. */
export interface RunFacts {
  runId: string;
  /** Which tenant owns this run. Read from the run's own record. */
  tenantId: string;
  /** The repository this run targets, `owner/name`. */
  repositoryFullName: string;
  /** The run's own status, from its state document. */
  status: RunStatus;
  /**
   * Which gates are open for a decision RIGHT NOW, from the queue's paused rows.
   * A gate absent from this list may not be decided, whatever the reason.
   */
  awaitingGates: readonly Gate[];
}

/**
 * The attempt, as it arrived. EVERY FIELD IS A BARE STRING ON PURPOSE.
 *
 * These come out of `JSON.parse` on a request body, so they are untrusted and
 * unvalidated -- typing `decision` as `"approved" | "rejected"` here would be a
 * lie the type system cannot catch at a JSON boundary, and would let an
 * unrecognised word reach `queue.resume`. Validation happens below, against an
 * exact list, with no case folding and no trimming: the same fail-closed rule
 * `approve_server._DECISIONS` and `graph.APPROVAL_WORDS` state, "on the three
 * prompts in this system where being misread is most expensive".
 */
export interface ApprovalAttempt {
  runId: string;
  gate: string;
  decision: string;
  reason: string;
}

/** Why an attempt was refused. The code is for the audit; the message is for a human. */
export type RefusalCode =
  | "cross-site-origin"
  | "not-authenticated"
  | "no-tenant"
  | "unknown-gate"
  | "unknown-decision"
  | "override-not-permitted-here"
  | "wrong-tenant"
  | "repository-not-in-scope"
  | "run-already-ended"
  | "gate-not-awaiting";

export interface Refusal {
  permitted: false;
  code: RefusalCode;
  /** Shown to the human who caused it. Never echoes a value they supplied. */
  message: string;
}

export interface Permit {
  permitted: true;
  runId: string;
  gate: Gate;
  decision: "approved" | "rejected";
  reason: string;
  /** WHO THE SERVER WILL ATTRIBUTE THIS TO. Not negotiable by the caller. */
  by: string;
  tenantId: string;
}

export type Authorisation = Permit | Refusal;

/** The three gates. Same tuple as `queue.GATES` and `HumanDecision.gate`. */
const GATES: readonly string[] = ["gate1", "gate2", "gate3"];

/**
 * What this surface will act on. NARROWER than `HumanDecision.decision`.
 *
 * `overridden` is deliberately absent. `approve_server.py` made the same trade and
 * stated the cost: "Overriding a security block is the single most dangerous thing
 * this vocabulary can express, and requiring shell access for it -- `gates_cli
 * resume ... --decision overridden` -- rather than an unauthenticated click is the
 * trade this module makes on purpose."
 *
 * A NETWORK ENDPOINT IS STRICTLY WEAKER THAN A SHELL, so it must not widen what a
 * loopback-only screen already refused. It is refused with its own code rather
 * than folded into `unknown-decision`, because "you named something real that this
 * surface will not do" and "you named nothing I recognise" are different facts and
 * an operator reading the audit needs to tell them apart.
 */
const DECISIONS: readonly string[] = ["approved", "rejected"];

/** A run in one of these has ENDED. `approve_server._TERMINAL`, same four. */
const TERMINAL: readonly RunStatus[] = [
  "rejected",
  "promoted",
  "blocked",
  "failed",
];

/**
 * May this attempt open this gate? The one entry point.
 *
 * `origin` is the request's `Origin` header, or `null` when absent. `session` is
 * `null` when nobody is signed in. Both are separate parameters rather than
 * fields on the attempt, because they are things the SERVER knows and the body
 * must not be able to carry.
 *
 * THE ORDER IS CHEAPEST-AND-BROADEST FIRST, and it is deliberate: an
 * unauthenticated cross-site probe is refused before any tenant is resolved, so
 * an anonymous caller cannot drive a database read -- the ordering
 * `infra/ingress/handler.py` establishes, where steps 1-3 precede the secret
 * fetch entirely so "an anonymous caller must not be able to drive
 * `GetSecretValue` calls against a public endpoint".
 */
export function decide(
  attempt: ApprovalAttempt,
  session: SessionIdentity | null,
  origin: string | null,
  facts: RunFacts | null,
  repositoriesInScope: readonly string[],
  allowedOrigins: readonly string[],
): Authorisation {
  if (!originIsAcceptable(origin, allowedOrigins)) {
    return {
      permitted: false,
      code: "cross-site-origin",
      message:
        "this request came from another site's page and was not acted on; " +
        "open the application directly to decide a gate",
    };
  }

  if (session === null) {
    return {
      permitted: false,
      code: "not-authenticated",
      message: "sign in to decide a gate. Nothing was recorded.",
    };
  }

  if (!session.tenantId.trim()) {
    return {
      permitted: false,
      code: "no-tenant",
      message:
        "your account is not attached to an organisation, so there is no scope " +
        "in which to decide. Nothing was recorded.",
    };
  }

  if (!GATES.includes(attempt.gate)) {
    return {
      permitted: false,
      code: "unknown-gate",
      message: `gate must be exactly one of ${GATES.join(", ")}. Nothing was recorded.`,
    };
  }

  if (attempt.decision === "overridden") {
    return {
      permitted: false,
      code: "override-not-permitted-here",
      message:
        "an override cannot be recorded from the web application. It is the one " +
        "decision that requires shell access: `python -m agentorg.gates_cli " +
        "resume <run_id> --gate <gate> --decision overridden --by <you>`. " +
        "Nothing was recorded.",
    };
  }

  if (!DECISIONS.includes(attempt.decision)) {
    return {
      permitted: false,
      code: "unknown-decision",
      message: `decision must be exactly one of ${DECISIONS.join(", ")}. Nothing else is read as a decision, and nothing was recorded.`,
    };
  }

  // THE RUN NOT EXISTING AND THE RUN BELONGING TO SOMEBODY ELSE GET THE SAME
  // ANSWER, and that is the disclosure decision rather than laziness. A run id is
  // an unguessable uuid, so `facts === null` here means either "no such run" or
  // "a run this tenant cannot see" -- and Lane B's leak suite records the same
  // convention: distinguishing them would itself be the disclosure. The caller
  // learns only what they already tried.
  if (facts === null || facts.tenantId !== session.tenantId) {
    return {
      permitted: false,
      code: "wrong-tenant",
      message: "no such run. Nothing was recorded.",
    };
  }

  // PER-REPOSITORY AUTHORISATION. Being in the tenant is not enough: a tenant may
  // have connected a repository and later removed it from scope, and a run
  // against a repository nobody has authorised must not be approvable.
  if (!repositoriesInScope.includes(facts.repositoryFullName)) {
    return {
      permitted: false,
      code: "repository-not-in-scope",
      message:
        "this run targets a repository that is not in your organisation's " +
        "scope. Nothing was recorded.",
    };
  }

  // ── THE `gates.resume` GAP, REFUSED AT THE BOUNDARY ─────────────────────────
  //
  // `gates.resume` sets `status="rejected"` for a rejection and NEVER un-sets it,
  // so approving a run the graph already rejected leaves `status="rejected"` while
  // still APPENDING the approval -- and `agentorg/timeline.py` then renders that
  // approval AFTER the rejection, so a rejected run displays a later approval on
  // the timeline the judges read. `status` holding is not a guard; nothing in
  // `gates.resume` refuses the attempt.
  //
  // It is refused HERE rather than in `gates.py` for the reason
  // `tests/test_approve_server.py:266-289` pins on purpose: a guard inside
  // `gates.resume` would revoke the documented `gates_cli resume --decision
  // overridden` override path, the one capability a human is meant to keep.
  //
  // TWO CHECKS, NOT ONE, AND THIS IS WHERE THIS MODULE DIVERGES FROM
  // `approve_server._apply` -- deliberately. That function uses a single
  // predicate (`_awaiting`) and argues, correctly, that one predicate is safer
  // than several. But it had ONE record to read: the log. Here there are TWO --
  // the queue's paused row (`awaitingGates`) and the run's state document
  // (`status`) -- written by different code at different times, and they CAN
  // disagree. A queue row still paused while the state says `rejected` is exactly
  // the gap above, and the status check is the half that closes it. Checking only
  // `awaitingGates` would trust the record that does not know about the
  // rejection.
  if (TERMINAL.includes(facts.status)) {
    return {
      permitted: false,
      code: "run-already-ended",
      message: `this run has already ended as ${facts.status}, so there is no decision left to make. Nothing was recorded.`,
    };
  }

  if (!facts.awaitingGates.includes(attempt.gate as Gate)) {
    return {
      permitted: false,
      code: "gate-not-awaiting",
      message: `this run is not awaiting a decision at ${attempt.gate} — it is already decided there, or it never paused there. Nothing was recorded.`,
    };
  }

  return {
    permitted: true,
    runId: facts.runId,
    gate: attempt.gate as Gate,
    decision: attempt.decision as "approved" | "rejected",
    reason: attempt.reason,
    // FROM THE SESSION, NEVER FROM THE BODY. The whole difference between this
    // surface and `approve_server`'s `by="ui-reviewer"`.
    by: session.login,
    tenantId: session.tenantId,
  };
}

/**
 * Refuse a POST that came from another site's page.
 *
 * ABSENT IS ALLOWED, and this needs stating because it looks like a hole.
 * `approve_server._check_origin` makes the same choice: "curl and the CLI send no
 * Origin, and the documented fallback path must keep working." A browser ALWAYS
 * sends `Origin` on a cross-site POST, so absent means not-a-browser, which is
 * not the threat this check addresses. The threat is a page in the operator's own
 * browser posting here, which loopback binding does not stop -- "that is the hole
 * loopback binding is most often assumed to close and does not."
 *
 * Matched on the exact origin string against a configured list, not on hostname
 * alone: this application is not loopback-only, so `https://evil.example` and
 * `https://app.example` differ in scheme and port as well as host, and a host-only
 * match would accept `http://` where only `https://` is served.
 */
export function originIsAcceptable(
  origin: string | null,
  allowedOrigins: readonly string[],
): boolean {
  if (origin === null || origin === "") {
    return true;
  }
  return allowedOrigins.includes(origin);
}
