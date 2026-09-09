/**
 * WHETHER A VERIFIED TOKEN IS A SESSION THIS APPLICATION ACTS ON. Pure, and
 * deliberately so.
 *
 * =========================================================================
 * THIS IS NOT A SECOND `web/lib/authz.ts`, AND THE DIVISION IS THE POINT.
 * =========================================================================
 * `authz.decide` answers *may THIS session open THIS gate on THIS run* — origin,
 * tenant match, repository scope, run status, awaiting gates. Ten refusal codes,
 * one entry point, the most scrutinised code in this repository. Nothing here
 * restates any of it, and the blank-tenant refusal in particular stays there:
 * `no-tenant` is declared once, in `authz.ts`, and this module does not carry a
 * second spelling of it.
 *
 * This module answers the question one layer down, which `authz` takes as already
 * settled by the time it is handed a `SessionIdentity`: **is there a session at
 * all, and is it one we admit?** Three facts, and they are the only three
 * decidable from the token alone with no run, no database and no clock read.
 *
 * =========================================================================
 * WHY THE ROLE CHECK LIVES HERE AND NOT IN `verifySession` — THE ONE PLACE
 * THIS LANE DIVERGES FROM THE REFERENCE IMPLEMENTATION, ON PURPOSE.
 * =========================================================================
 * `~/sorour/AgentsforHumansHackathon/web/lib/cognito.ts` refuses a wrong role
 * inside `verifySession`, so its `verifySession` answers "is this a caseworker"
 * and "is this token authentic" with one `null`. That is right *there*, because
 * that pool sets `AllowAdminCreateUserOnly: true` — "a caseworker account is
 * issued, not requested" — so every account that exists already carries the role
 * and the two questions cannot come apart.
 *
 * **Here they can, and requirement 9 is why.** Self-service sign-up is a stated
 * requirement this project is judged on, and a person who has just signed up has
 * no `custom:role` and no `custom:tenant` — those are set by an administrator,
 * which is precisely what makes them trustworthy. Collapsing the two questions
 * would make that account read as **not signed in**: `/api/session` would answer
 * `signed_in: false`, the screen would show a sign-in button, the button would
 * send them round Cognito and back to the same answer, and nothing anywhere would
 * say why. That is this repository's signature defect — a state that cannot
 * distinguish "did not happen" from "happened and was refused" — arriving on the
 * front door.
 *
 * So `verifySession` establishes **authenticity** and this function establishes
 * **admissibility**, and the two refusals are separately nameable:
 *
 *     no session          nobody is signed in
 *     session-expired     signed in, and the token is past its expiry
 *     no-role             the account exists and has not been assigned a role
 *     wrong-role          the account holds a role this application does not admit
 *
 * Nothing is weakened by the split, and the reason is where the checks sit rather
 * than a claim about them: `web/lib/session.ts`'s `currentIdentity()` is the ONLY
 * way any route obtains an identity, and it returns `null` unless this function
 * permits **and** the tenant claim is well-formed. So a role-less token
 * authenticates and authorises nothing. The one surface that sees it is
 * `/api/session`, which is unauthenticated by design because its whole answer may
 * be "nobody is signed in".
 *
 * WHY PURE, AND WHY `nowMs` IS AN ARGUMENT
 * ========================================
 * `authz.ts`'s reasoning, unchanged: a check that performs its own I/O can be
 * bypassed by a caller that fetches differently, and one that reads its own clock
 * cannot be driven to its boundary by a test. Both facts arrive as parameters.
 */

/**
 * The role claim this application admits, and the ONE place the word is written.
 *
 * `web/lib/cognito.ts` imports it rather than typing `"reviewer"` a second time —
 * a value compared in two places is two declarations of one fact, and they keep
 * agreeing while one moves. Same discipline as `scoring.policy_severity`, which
 * the gitleaks wrapper calls rather than typing `critical`.
 *
 * `reviewer` is the word CLAUDE.md's own seeding SQL writes into
 * `membership.role`, so a person who exists in the pool and in the database
 * carries one spelling in both.
 *
 * COMPARED EXACTLY. `"Reviewer"`, `"reviewer "` and `"reviewers"` are not this
 * role. Case folding or trimming here is the failure `graph.APPROVAL_WORDS`
 * refuses on the prompts where being misread is most expensive, and the reference
 * measured its cost in the neighbouring project: `"Escalate."` — one trailing
 * period — resumed a blocked tool and filed a renewal for a household missing a
 * required document.
 */
export const REVIEWER_ROLE = "reviewer";

/**
 * What a verified Cognito ID token establishes. Every field is signature-verified
 * against the pool's published JWKS before it reaches this type.
 *
 * `sub` is the pool's opaque subject — the one identifier that survives a rename
 * and that an operator can match against the pool.
 *
 * `login` becomes `HumanDecision.by`, which is the whole difference between this
 * surface and `approve_server`'s constant `by="ui-reviewer"`. It comes from
 * `cognito:username`. **The pool's usernames must therefore be handles rather
 * than email addresses**: `by` is written to `runs/<run_id>.jsonl` and to the
 * DynamoDB audit trail, and neither has a deletion path — `Scan`, `DeleteItem`
 * and `BatchWriteItem` are deliberately absent from that table's IAM grant. That
 * is a property of how the pool is provisioned and not something this file can
 * enforce, so it is stated here and in the lane's report rather than turned into
 * a refusal: a session that silently becomes `null` because somebody's username
 * contains an `@` is the same unreadable failure the split above exists to avoid.
 *
 * `role` and `tenantId` are `""` when the claim is absent — a freshly signed-up
 * account, before an administrator assigns either. They are never guessed and
 * never defaulted to a real value.
 *
 * `expiresAt` is milliseconds since the epoch, already multiplied out of the
 * token's `exp` seconds.
 */
export interface TokenIdentity {
  sub: string;
  login: string;
  role: string;
  tenantId: string;
  expiresAt: number;
}

/** Why a session was refused. The code is for the audit; the message for a human. */
export type SessionRefusalCode =
  | "no-session"
  | "session-expired"
  | "no-role"
  | "wrong-role";

export interface SessionRefusal {
  permitted: false;
  code: SessionRefusalCode;
  /** Shown to the person who caused it. Never echoes a value they supplied. */
  message: string;
}

export interface SessionPermit {
  permitted: true;
  identity: TokenIdentity;
}

export type SessionAuthorisation = SessionPermit | SessionRefusal;

function refuse(code: SessionRefusalCode, message: string): SessionRefusal {
  return { permitted: false, code, message };
}

/**
 * Is this a session this application will act on? The one entry point.
 *
 * `identity` is `null` when `verifySession` refused, which it does for every
 * cryptographic and claim-shape failure — there is no middle value between an
 * identity and nothing.
 *
 * THE EXPIRY IS CHECKED HERE **AND** IN `cognito.ts`, AND THAT IS DEFENCE IN
 * DEPTH RATHER THAN DUPLICATION. `jose` enforces `exp` during verification and
 * `verifySession` refuses a non-finite one — but this function is reachable with
 * any `TokenIdentity` a caller can construct, and the expiry is the one field
 * where being wrong is silent: `Infinity <= nowMs` is `false`, so a token whose
 * `exp` parses to `Infinity` yields a session no `<=` comparison can ever call
 * expired. The reference measured all three halves of that hazard —
 * `JSON.parse('{"exp":1e400}').exp` is `Infinity`, `typeof` it is `"number"`, and
 * `jose` **verifies** such a token — which is why `Number.isFinite` and not
 * `typeof exp === "number"` is the check in both places.
 */
export function authorizeSession(
  identity: TokenIdentity | null,
  nowMs: number,
): SessionAuthorisation {
  if (identity === null) {
    return refuse(
      "no-session",
      "sign in to use this application. Nothing was recorded.",
    );
  }

  // BOTH SIDES MUST BE FINITE. `NaN <= nowMs` and `Infinity <= nowMs` are each
  // `false`, so a malformed expiry slips past the comparison below and reads as a
  // session that never expires. `nowMs` is checked in the same breath and for the
  // mirror reason: a caller passing `NaN` for the clock makes every session look
  // live, and that argument comes from outside this module.
  if (!Number.isFinite(identity.expiresAt) || !Number.isFinite(nowMs)) {
    return refuse("session-expired", "your session expired. Sign in again.");
  }

  // `<=` and not `<`: a session expiring exactly now IS expired. Written the
  // other way a just-expired session is honoured, which is the fail-open
  // direction on the one surface in this repository that can open a security
  // gate over a network.
  if (identity.expiresAt <= nowMs) {
    return refuse("session-expired", "your session expired. Sign in again.");
  }

  // AN UNASSIGNED ACCOUNT AND AN UNADMITTED ONE ARE DIFFERENT FACTS, and they get
  // different codes for `scan_provenance`'s reason: collapsing "nobody has set
  // this yet" into "this value is not one we accept" hides a pending
  // administrative action behind what reads as a rejection. The first is the
  // normal state of every account the moment it signs itself up; the second means
  // somebody assigned a role this application does not act on.
  if (identity.role.trim() === "") {
    return refuse(
      "no-role",
      "this account has not been assigned a role yet, so it cannot act on runs. " +
        "An administrator assigns one. Nothing was recorded.",
    );
  }

  if (identity.role !== REVIEWER_ROLE) {
    // NAMES NEITHER THE ROLE HELD NOR THE ONE REQUIRED. `ApiError`'s rule is that
    // a message never echoes a value the caller supplied, and telling an
    // unadmitted account which claim value would admit it is an instruction.
    return refuse(
      "wrong-role",
      "this account may not act on runs. Nothing was recorded.",
    );
  }

  return { permitted: true, identity };
}
