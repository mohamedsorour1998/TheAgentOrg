/**
 * WHICH TENANT A PERSON BELONGS TO. The bootstrap, and why it cannot be scoped.
 *
 * =========================================================================
 * THIS IS THE ONE QUERY THIS LAYER RUNS THAT IS NOT TENANT-SCOPED, AND IT
 * CANNOT BE. IT IS WHAT PRODUCES THE SCOPE.
 * =========================================================================
 * `web/lib/pipeline.ts` argues at length that this layer must not answer a tenancy
 * question with its own SQL, and that argument holds for every DATA read. This one
 * is different in kind, and the difference is structural rather than an exception
 * being carved out:
 *
 *   `engine.acting_as(tenant_id)` needs a tenant id. Something must produce one,
 *   and that something cannot itself require one — a scoped accessor answering
 *   "which tenant am I in" would need the answer as its input.
 *
 * Lane B recognises the same shape from the other side: there is deliberately NO
 * tenant-scoped accessor that reads `app_user`, because "no caller can enumerate the
 * user base", and `accessors.list_members(scope)` answers "who is in THIS tenant" —
 * the inverse of the question here. Lane K has the identical bootstrap:
 * `auth.resolve()` reads a credential with no tenant bound and PRODUCES the
 * `Credential` every scoped call then carries.
 *
 * So this is the session equivalent of `auth.resolve()`, and everything after it
 * goes through the Python accessors with the result bound.
 *
 * WHAT KEEPS IT FROM BEING A HOLE, precisely:
 *
 *   * IT IS KEYED ON THE SESSION'S OWN LOGIN, which the caller cannot choose — it
 *     comes from a verified Auth.js session, never from a request body.
 *   * IT RETURNS ONLY MEMBERSHIPS THAT LOGIN HOLDS. The `WHERE` clause is on
 *     `web_identity.github_login`, so the result set is the asker's own rows.
 *   * IT READS NO RUN, NO SECRET, NO BUDGET AND NO REPOSITORY. Two columns from two
 *     tables: which tenants this person is a member of, and their role.
 *   * IT NEVER WRITES. There is no INSERT, UPDATE or DELETE in this file.
 *
 * PARAMETERISED, ALWAYS. `github_login` arrives from a GitHub profile — not from a
 * request body, but not authored by us either — and a login is the kind of value an
 * attacker controls by choosing a username. `$1` rather than interpolation, which is
 * why this takes a query function rather than building a string.
 *
 * WHY IT TAKES A QUERY FUNCTION RATHER THAN A POOL
 * ===============================================
 * So the SQL and every refusal below are testable with NO POSTGRES. No Postgres has
 * ever been connected in this repository — `agentorg/db/engine.py` is sqlite3-only
 * and Lane B's own note says nothing in the suite connects to one — so a version of
 * this that took a `Pool` would be entirely unexercised, and its refusals would be
 * confidence that cannot be falsified.
 */

/** The rows this module reads. Two columns, named explicitly. */
export interface MembershipRow {
  tenant_id: string;
  role: string;
}

/**
 * Whatever can run a parameterised query. `pg.Pool` satisfies it structurally, and
 * so does a test double — which is the point.
 */
export interface QueryRunner {
  query(sql: string, values: readonly unknown[]): Promise<{ rows: MembershipRow[] }>;
}

/**
 * The query. NAMED COLUMNS, never `SELECT *`.
 *
 * Lane B measured why: `dict(sqlite3.Row)` "silently collapses duplicate column
 * names, keeping the first of each pair, nothing raised" — a `membership JOIN
 * app_user` shares `id` and `created_at`, so an unaliased `SELECT *` returns a dict
 * with the user's id simply gone, and the result still looks like a member. The
 * equivalent here is `pg` returning one `id` where two were selected. Naming the two
 * columns this module actually reads makes that impossible.
 *
 * ORDERED, so a person in several organisations resolves to the SAME one on every
 * request. Without `ORDER BY` the answer is whichever row Postgres returned first,
 * which can change between requests — and a tenant that changes under a person
 * mid-session means their run list changes with no action from them. `created_at`
 * ascending means the oldest membership wins, which is stable and explicable;
 * `tenant_id` breaks a tie so two memberships created in the same transaction do
 * not reintroduce the ambiguity.
 */
export const MEMBERSHIP_QUERY = `
  SELECT m.tenant_id AS tenant_id, m.role AS role
    FROM web_identity w
    JOIN membership m ON m.user_id = w.app_user_id
   WHERE w.github_login = $1
   ORDER BY m.created_at ASC, m.tenant_id ASC
`;

/**
 * Every tenant this login is a member of, oldest membership first.
 *
 * An empty array is a REAL ANSWER: a person who has signed in and belongs to no
 * organisation. The caller refuses with `no-tenant` rather than inventing one.
 */
export async function membershipsFor(
  runner: QueryRunner,
  githubLogin: string,
): Promise<readonly MembershipRow[]> {
  const login = githubLogin.trim();
  if (login === "") {
    // A BLANK LOGIN IS NOT QUERIED. `w.github_login = ''` would match any row whose
    // login was written blank, and this result decides a tenant scope. Refused
    // before the query rather than trusted to return nothing.
    return [];
  }
  const result = await runner.query(MEMBERSHIP_QUERY, [login]);
  return result.rows.filter((row) => row.tenant_id.trim() !== "");
  // The filter is not decoration: `engine.acting_as` REFUSES a blank tenant, so a
  // blank row reaching the caller becomes an exception several layers away, in a
  // stack trace naming a context manager rather than a malformed membership row.
}

/**
 * The single tenant to act as, or `null`.
 *
 * ONE TENANT PER SESSION TODAY, AND THE MULTI-TENANT CASE IS NAMED RATHER THAN
 * GUESSED. `app_user` exists precisely because "one person may hold memberships in
 * several organisations", so this genuinely happens — and picking silently is the
 * wrong answer twice over: the person cannot tell which organisation they are
 * looking at, and a gate they approve is recorded against a tenant they did not
 * choose.
 *
 * The oldest membership is chosen because SOMETHING must be, and `null` for a person
 * with two organisations would lock out a legitimate user. It is stable and
 * explicable, and the honest fix is a tenant switcher — a screen, which is Lane J's,
 * plus a cookie this function would read. Recorded as a further step rather than
 * half-built.
 */
export function soleTenant(rows: readonly MembershipRow[]): string | null {
  const first = rows[0];
  return first === undefined ? null : first.tenant_id;
}
