/**
 * WHO IS ASKING, AND WHICH TENANT THEY ARE IN. Every route starts here.
 *
 * The one function that turns a request into a `SessionIdentity`, which is what
 * `authz.decide` takes. Nothing else in `web/app/api/**` may construct one — a
 * route that built its own could build one from a request body, and a tenant a
 * caller can name is a tenant a caller can choose. That rule is unchanged by the
 * move to Cognito; only where the identity COMES FROM has changed.
 *
 * THE TENANT NOW ARRIVES ON THE TOKEN, AND THE PREVIOUS VERSION OF THIS FILE
 * ARGUED AGAINST EXACTLY THAT. Read `web/lib/tenant.ts` before deciding it is
 * an improvement: it closes the RLS circularity, it moves the assignment from a
 * SQL row to a Cognito attribute, and it reintroduces a revocation latency this
 * file used to refuse — bounded now by `cognito.SESSION_MAX_AGE_SECONDS`, one
 * hour, rather than by a thirty-day database session. All three are true at once
 * and the honest account needs all three.
 *
 * A PERSON IN NO ORGANISATION GETS `null`, NEVER A DEFAULT TENANT — unchanged,
 * and it matters more now that anyone may sign themselves up. `engine.acting_as`
 * refuses a blank scope because "a blank scope matches a blank column and that is
 * a row nobody owns", and `tenant_zero.for_run_state` translates a blank to
 * tenant zero — correct for a RUN written before multi-tenancy and catastrophic
 * for a SESSION, because it would hand every new signup the original single-tenant
 * deployment's runs.
 *
 * THREE LAYERS, AND EACH ANSWERS A DIFFERENT QUESTION:
 *
 *     cognito.verifySession   is this token authentic, and about a real account?
 *     authorize.authorizeSession   is this an account this application acts on?
 *     tenant.tenantFromClaim  is there a scope to act in?
 *
 * `currentIdentity` requires all three and returns `null` otherwise, so a
 * signed-in-but-unassigned account authenticates and authorises nothing.
 */

import { cookies } from "next/headers";

import { authorizeSession, type TokenIdentity } from "./authorize";
import { SESSION_COOKIE, verifySession } from "./cognito";
import { tenantFromClaim } from "./tenant";
import type { SessionIdentity } from "./authz";

/**
 * The verified token, or `null`. **`/api/session` is the ONLY intended caller.**
 *
 * It is exported because that route has to distinguish *nobody is signed in* from
 * *signed in and not yet assigned a role or a tenant* — a distinction requirement
 * 9 creates the moment self-service sign-up is allowed, and one `currentIdentity`
 * deliberately collapses. No route that reads or writes data may use this: it
 * carries an identity that has passed authentication and no authorisation check
 * at all.
 *
 * THE `catch` IS FAIL-CLOSED AND ITS REACHABILITY IS THE INTERESTING PART.
 * `cookies()` throws outside a request scope, so it sits INSIDE the `try` rather
 * than above it — Plan 1's `list_documents` lesson from the reference project:
 * "this function already fails closed" is not the same claim as "every line in it
 * is inside the `try`". `verifySession` itself is believed not to throw for any
 * input (`issuer()` and `clientId()` are called inside its own `try`), which
 * makes this branch mostly unreachable today — and an unreachable branch is still
 * shipped code that the next edit to `cognito.ts` can make live.
 */
export async function verifiedToken(): Promise<TokenIdentity | null> {
  try {
    const jar = await cookies();
    return await verifySession(jar.get(SESSION_COOKIE)?.value);
  } catch {
    // A verifier that could not run has authenticated nobody.
    return null;
  }
}

/**
 * The signed-in identity a route may act on, or `null`.
 *
 * Returns `null` for four situations, and collapsing them is deliberate AT THIS
 * LAYER: no cookie, a token that fails verification, an account with no admitted
 * role, and an account with no usable tenant claim. The caller turns them into
 * refusal codes through `authz.decide` — this function's job is only to refuse to
 * invent an identity, and a route that could tell the four apart would be a route
 * that could tell an unauthenticated caller which accounts exist.
 *
 * `login` NOT `email`, and NOT `sub`. It becomes `HumanDecision.by`, and it is
 * what a person recognises on a timeline beside a gate decision — the whole
 * difference between this surface and `approve_server`'s `by="ui-reviewer"`. An
 * email would additionally be personal data in the append-only decision log,
 * which is `runs/<run_id>.jsonl` and a DynamoDB audit trail — neither of which has
 * a deletion path, because `Scan`, `DeleteItem` and `BatchWriteItem` are
 * deliberately absent from that table's IAM grant. See `authorize.TokenIdentity`
 * for the constraint that places on how the pool is provisioned.
 */
export async function currentIdentity(): Promise<SessionIdentity | null> {
  const identity = await verifiedToken();
  const authorised = authorizeSession(identity, Date.now());
  if (!authorised.permitted) {
    /**
     * A GITHUB SIGN-IN, WHICH CARRIES NO COGNITO TOKEN AT ALL.
     *
     * Cognito cannot federate GitHub — GitHub is OAuth2 and issues no `id_token` —
     * so a GitHub session is a separate cookie with a separate verifier and its own
     * key. **The two are never handed to one verifier**, because a verifier that
     * accepts both RS256 and HS256 can be given a token signed with HMAC using the
     * published RSA public key as the secret, and it verifies. See
     * `lib/github-session.ts`.
     *
     * TRIED SECOND, so an email/password session keeps winning where both cookies
     * somehow exist. The callback clears the other cookie on every GitHub sign-in,
     * so that state should not arise — and "should not arise" is exactly the
     * reasoning that makes an order worth fixing explicitly rather than leaving to
     * whichever branch runs first.
     *
     * `readSession` returns `null` for every failure, so a tampered, expired or
     * unconfigured GitHub cookie falls through to the same `null` this function
     * already returns for the four Cognito cases.
     */
    const { GITHUB_SESSION_COOKIE, readSession } = await import("./github-session");
    const jar = await cookies();
    const github = await readSession(jar.get(GITHUB_SESSION_COOKIE)?.value);
    if (github === null) {
      return null;
    }
    const scoped = tenantFromClaim(github.tenantId);
    if (scoped === null) {
      return null;
    }
    return { login: github.login, tenantId: scoped };
  }

  // The tenant is validated rather than trusted for its shape: `tenantFromClaim`
  // refuses blank, over-long and control characters, so a malformed claim fails
  // here rather than several layers away inside a Python context manager.
  const tenantId = tenantFromClaim(authorised.identity.tenantId);
  if (tenantId === null) {
    return null;
  }

  return { login: authorised.identity.login, tenantId };
}

/**
 * Which repositories this tenant may act on. UNCHANGED by the Cognito move.
 *
 * Reads Lane B's `repository` table through its scoped accessor, via the Python
 * reader — never with a query written here. See `web/lib/pipeline.ts`.
 *
 * AN EMPTY LIST IS A REAL ANSWER and `authz.decide` refuses against it, so a
 * tenant that has connected nothing cannot approve anything. Same direction as
 * Lane K's empty key store and `budgets.check` with no budget row: absent must
 * not read as unlimited.
 */
export async function repositoriesInScope(
  tenantId: string,
): Promise<readonly string[]> {
  const { readPipeline } = await import("./pipeline");
  const answer = await readPipeline<{ repositories: { full_name: string }[] }>(
    "runs",
    { action: "list_repositories", tenant_id: tenantId },
  );
  return answer.repositories.map((row) => row.full_name);
}
