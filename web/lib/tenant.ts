/**
 * WHICH TENANT A PERSON BELONGS TO — now a verified claim, not a database read.
 *
 * =========================================================================
 * WHAT THIS CHANGE CLOSES, AND WHAT IT MOVES. READ BOTH LISTS BEFORE
 * QUOTING EITHER, BECAUSE THEY ARE NOT THE SAME LIST.
 * =========================================================================
 *
 * WHAT IT CLOSES — the circularity, and only the circularity.
 *
 * The previous version of this file ran one query and could not scope it. Its
 * whole argument is worth keeping, because the shape recurs: `membershipsFor`
 * read `membership` to discover the tenant, `membership` is in
 * `schema.SCOPED_TABLES` and therefore carries an RLS policy comparing against
 * `current_setting('agentorg.tenant_id')`, RLS needs a bound tenant to return a
 * row, and the bound tenant was the thing the function was trying to discover.
 * Measured 2026-08-28, one connection, same query, same role:
 *
 *     no tenant bound      -> []
 *     tenant-zero bound    -> [('tenant-zero',)]
 *
 * So `/api/session` answered `signed_in: true` with `tenant_id: null` and every
 * authenticated route 401'd. That loop is **gone**, and not by loosening
 * anything: the tenant now arrives on the ID token, signature-verified against
 * the pool's published JWKS, so nothing has to be read to discover it. The three
 * options that file recorded — take `membership` out of `SCOPED_TABLES`, admit
 * rows when no tenant is bound, or give the identity lookup its own unscoped path
 * — are all moot, including (c), which was the right one and which nobody now has
 * to build.
 *
 * WHAT IT DOES **NOT** CLOSE. CLAUDE.md's open item 1 is titled *"Two-role
 * database model — the app must connect as a non-owning role"*, and that is a
 * deployment decision in `infra/`, not a line of TypeScript. CLAUDE.md measures
 * the current state plainly: "THE CONTAINER CONNECTS AS A SUPERUSER, SO RLS
 * CONSTRAINS NOTHING THERE." Nothing in this file changes which role the DSN
 * names. What this change does is **remove the reason that role could not be
 * fixed** — with the circularity gone, switching the DSN to a plain non-owning
 * LOGIN role no longer breaks sign-in.
 *
 * WHAT IT MOVES, which is the part most easily mis-stated as a win:
 *
 *   * **Assignment moves from SQL to Cognito.** Somebody still has to decide
 *     which tenant a person is in; it is now `admin-set-user-attributes` instead
 *     of an `INSERT INTO membership`. A better place — server-side and
 *     signature-verified — not an absent one.
 *   * **`membership` and `custom:tenant` are now two declarations of one fact,
 *     and NOTHING RECONCILES THEM.** This repository's own rule about second
 *     declarations applies: they will keep agreeing while one moves. There is no
 *     reconciliation check in this lane and building one with no caller would be
 *     the "correct answer nobody asks for" pattern, so it is named here instead.
 *   * **Revocation latency is reintroduced, and this file's predecessor argued
 *     against exactly that.** `web/lib/session.ts` said: "Carrying it on the
 *     session would be cheaper and is wrong: revoking somebody's membership would
 *     leave their live session still scoped to the tenant they were removed from,
 *     for up to thirty days, with nothing anywhere saying so." A token claim has
 *     precisely that property. The bound is the token's lifetime, which is why
 *     `cognito.SESSION_MAX_AGE_SECONDS` is **one hour** rather than thirty days
 *     and is documented there as a security setting. Removing a person from a
 *     tenant is therefore effective within an hour rather than immediately.
 *
 * =========================================================================
 * WHAT THE CLAIM TRUSTS, AND THE TWO POOL SETTINGS THAT MAKE IT TRUSTWORTHY
 * =========================================================================
 * A tenant claim is only as good as who can set it, and self-service sign-up is
 * allowed here (requirement 9), so the pool — not this file — is what stops a
 * caller choosing their own tenant. Two settings, and they guard **different
 * verbs**:
 *
 *   * `custom:tenant` declared `Mutable: False` in the pool's schema stops
 *     `UpdateUserAttributes` — a signed-in user rewriting the claim that
 *     authorises them.
 *   * `custom:tenant` **excluded from the app client's `WriteAttributes`** stops
 *     `SignUp` from setting it in the first place. `Mutable: False` does **not**
 *     help here: an immutable attribute set at creation is set forever, so a
 *     self-signup that could name its own tenant would lock the wrong answer in.
 *
 * The reference deployment measured that omitting `WriteAttributes` entirely
 * **grants everything** rather than withholding it — an ungranted mutable custom
 * attribute was written successfully by a signed-in user — so the list must be
 * present and explicit, not absent. Neither setting is verified from here: this
 * lane provisions no pool, and `infra/` belongs to Lane Q. **This file relies on
 * both and can enforce neither**, which is why it is written down rather than
 * assumed.
 *
 * What this file CAN do is refuse a claim that is not the shape of a tenant id,
 * so a malformed value fails here rather than several layers away inside a
 * Python context manager. That is the whole of `tenantFromClaim`.
 */

/**
 * The longest tenant id this application will carry.
 *
 * Not a guess about a schema: it is a bound on a value that crosses a JSON
 * subprocess boundary into `web/lib/reader/*.py` and ends up in an append-only
 * log. 255 is longer than any identifier this repository issues (`tenant-zero`,
 * a uuid, an organisation slug) and short enough that a claim carrying a payload
 * is refused rather than written.
 */
export const MAX_TENANT_ID_LENGTH = 255;

/**
 * A `custom:tenant` claim as a usable scope, or `null`.
 *
 * `null` HAS TWO CAUSES AND THAT IS DELIBERATE at this layer: the claim is absent
 * (a signed-up account nobody has assigned yet — the normal state of every new
 * account) or the claim is malformed. Both mean the same thing to every caller —
 * there is no scope to act in — and `authz.decide` refuses both with `no-tenant`,
 * which is the one declaration of that refusal. Splitting them here would put a
 * second spelling of `no-tenant` in a second file.
 *
 * WHAT IS REFUSED, AND WHAT IS DELIBERATELY NOT:
 *
 *   * **Blank** is refused. `engine.acting_as` refuses it too — "a blank scope
 *     matches a blank column and that is a row nobody owns" — and a blank
 *     reaching that far becomes a `ValueError` in a stack trace naming a context
 *     manager rather than a malformed claim. Refused here, where it can be named.
 *   * **Control characters** are refused, newline and tab included. A tenant id
 *     travels as a JSON argument to a Python subprocess and lands in a log line;
 *     no legitimate identifier contains one, and a value that can forge a log row
 *     is worth refusing at the boundary.
 *   * **Over-long** is refused, per `MAX_TENANT_ID_LENGTH`.
 *   * **INTERIOR SPACES AND UNUSUAL PUNCTUATION ARE ALLOWED**, and that is a
 *     decision rather than an omission. `agentorg/db/engine.py:81` imposes
 *     exactly one rule — not blank — so a stricter pattern here would be this
 *     application inventing a constraint the database half does not share, and
 *     the symptom would be a legitimately-issued tenant silently producing a
 *     null session with nothing saying which of the two halves refused it. A
 *     refusal nobody can read is the failure this repository exists to prevent.
 */
export function tenantFromClaim(value: string | undefined | null): string | null {
  const tenant = (value ?? "").trim();
  if (tenant === "") return null;
  if (tenant.length > MAX_TENANT_ID_LENGTH) return null;
  // Code points rather than a regex, on purpose twice over: a character class
  // spelling this needs either literal control BYTES in the source -- invisible in
  // every diff and every review, which is how the first draft of this line shipped --
  // or `\x00-\x1f` escapes, which `no-control-regex` refuses and which would need a
  // lint suppression on a security check. A loop over code points needs neither.
  for (const character of tenant) {
    const code = character.codePointAt(0) ?? 0;
    if (code < 0x20 || code === 0x7f) return null;
  }
  return tenant;
}
