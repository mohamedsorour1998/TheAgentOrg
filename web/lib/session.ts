/**
 * WHO IS ASKING, AND WHICH TENANT THEY ARE IN. Every route starts here.
 *
 * The one function that turns a request into a `SessionIdentity`, which is what
 * `authz.decide` takes. Nothing else in `web/app/api/**` may construct one — a
 * route that built its own could build one from a request body, and a tenant a
 * caller can name is a tenant a caller can choose.
 *
 * THE TENANT IS RESOLVED PER REQUEST, FROM MEMBERSHIP, NOT CARRIED ON THE SESSION
 * =============================================================================
 * A session identifies the PERSON. The tenant comes from joining
 * `web_identity` -> `app_user` -> `membership` on every request.
 *
 * Carrying it on the session would be cheaper and is wrong: revoking somebody's
 * membership would leave their live session still scoped to the tenant they were
 * removed from, for up to thirty days, with nothing anywhere saying so. That is the
 * same shape as a JWT session that cannot be revoked, one level up — and it is why
 * `authConfig` uses a database strategy in the first place.
 *
 * A PERSON IN NO ORGANISATION GETS `null`, NEVER A DEFAULT TENANT. `engine.acting_as`
 * refuses a blank scope because "a blank scope matches a blank column and that is a
 * row nobody owns", and `tenant_zero.for_run_state` translates a blank to tenant
 * zero — which is correct for a RUN written before multi-tenancy and catastrophic
 * for a SESSION: it would hand anybody who signs in the original single-tenant
 * deployment's runs. So the translation happens for run state and never for a
 * session, and the reader refuses a blank tenant at its own boundary as well.
 */

import { auth } from "./auth";
import type { SessionIdentity } from "./authz";

/**
 * The signed-in identity, or `null`.
 *
 * Returns `null` for three different situations, and that is deliberate at THIS
 * layer: no session, a session with no GitHub login, and a session whose person
 * belongs to no organisation. The caller turns them into different statuses (401 vs
 * 403) through `authz.decide`'s refusal codes — this function's job is only to
 * refuse to invent an identity.
 *
 * `login` NOT `email`. It becomes `HumanDecision.by`, and it is what a person
 * recognises on a timeline beside a gate decision. An email is also personal data
 * that would then appear in the append-only decision log, which is
 * `runs/<run_id>.jsonl` and a DynamoDB audit trail — neither of which has a
 * deletion path, because `Scan`, `DeleteItem` and `BatchWriteItem` are deliberately
 * absent from that table's IAM grant.
 */
export async function currentIdentity(): Promise<SessionIdentity | null> {
  const session = await auth();
  if (!session?.user) {
    return null;
  }

  const login = githubLoginFrom(session.user);
  if (login === null) {
    // A session with no usable login cannot attribute a decision, and this surface
    // exists to attribute decisions. Refused rather than falling back to an email
    // or a database id: `approve_server`'s `by="ui-reviewer"` is the failure this
    // whole lane exists to fix, and a fallback would reintroduce it with a
    // different constant.
    return null;
  }

  const tenantId = await tenantForLogin(login);
  if (tenantId === null) {
    return null;
  }

  return { login, tenantId };
}

/**
 * The GitHub login off an Auth.js user, or `null`.
 *
 * Auth.js's `Session["user"]` declares `name`, `email` and `image` and NOT a
 * provider login, so the login is carried through a callback into `name` or read
 * from `web_identity`. This function reads the field and refuses a blank rather
 * than trusting it to be populated — a blank `by` recorded against a gate decision
 * is the same defect as a constant one.
 */
function githubLoginFrom(user: { name?: string | null }): string | null {
  const login = (user.name ?? "").trim();
  return login === "" ? null : login;
}

/**
 * Which tenant this login belongs to, or `null`.
 *
 * Delegates to `web/lib/tenant.ts`, which carries the argument for why this one
 * query is not tenant-scoped and cannot be: it is what PRODUCES the scope, the way
 * Lane K's `auth.resolve()` produces the `Credential` everything scoped then
 * carries.
 *
 * `null` FOR A PERSON IN NO ORGANISATION, never tenant zero. That translation is
 * correct for a RUN written before multi-tenancy and catastrophic for a SESSION: it
 * would hand anybody who signs in the original single-tenant deployment's runs. The
 * first failure is a person reading "your account is not attached to an
 * organisation"; the second is a breach that looks like a working product.
 */
async function tenantForLogin(login: string): Promise<string | null> {
  const { membershipsFor, soleTenant } = await import("./tenant");
  const { sessionPool } = await import("./auth");
  return soleTenant(await membershipsFor(sessionPool(), login));
}

/**
 * Which repositories this tenant may act on.
 *
 * Reads Lane B's `repository` table through its scoped accessor, via the Python
 * reader — never with a query written here. See `web/lib/pipeline.ts`.
 *
 * AN EMPTY LIST IS A REAL ANSWER and `authz.decide` refuses against it, so a tenant
 * that has connected nothing cannot approve anything. Same direction as Lane K's
 * empty key store and `budgets.check` with no budget row: absent must not read as
 * unlimited.
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
