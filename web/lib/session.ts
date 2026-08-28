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
 * =========================================================================
 * NOT IMPLEMENTED, AND IT RETURNS `null` RATHER THAN A GUESS.
 * =========================================================================
 * Resolving this needs a query across `web_identity` -> `app_user` -> `membership`,
 * and the membership half is Lane B's `accessors.list_members` — which takes a
 * `TenantScope`, i.e. it answers "who is in THIS tenant" and cannot answer "which
 * tenant is this person in". That inversion is deliberate on Lane B's side: there
 * is NO tenant-scoped accessor that reads `app_user`, precisely so no caller can
 * enumerate the user base.
 *
 * So the honest options are a new Lane B accessor (`tenant_for_user`, unscoped by
 * necessity, with a written `unscoped_reason`) or a direct read of `web_identity`
 * joined to `membership` from this layer. The second is available today and is
 * REFUSED: it would be this layer answering a tenancy question with its own SQL,
 * which is exactly what `web/lib/pipeline.ts` argues against at length.
 *
 * **Returning `null` means every authenticated route refuses with `no-tenant` until
 * this lands.** That is the fail-closed direction and it is visible immediately —
 * as opposed to returning tenant zero, which would work beautifully in the
 * single-tenant demo and silently hand every new signup the original deployment's
 * runs. The first failure is a person seeing "your account is not attached to an
 * organisation"; the second is a data breach that looks like a working product.
 */
async function tenantForLogin(_login: string): Promise<string | null> {
  return null;
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
  _tenantId: string,
): Promise<readonly string[]> {
  // Deliberately empty until the reader action lands, for `tenantForLogin`'s
  // reason: an invented list would permit an approval against a repository nobody
  // authorised, and it would do it silently.
  return [];
}
