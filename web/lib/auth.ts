/**
 * AWS COGNITO, NOT AUTH.JS. What this file is now, and what it is left holding.
 *
 * =========================================================================
 * THE DECISION THIS FILE USED TO ARGUE HAS BEEN REVERSED, DELIBERATELY.
 * =========================================================================
 * The previous version opened by defending **Auth.js, not Cognito**: "Cognito is
 * the quickest path to working auth on AWS and it collides with requirement 4 —
 * a demonstration where the stack comes up on the operator's own machine. With
 * Cognito in the auth path that demonstration either cannot sign anyone in, or
 * needs a second auth implementation for the self-hosted case: **two code paths
 * on a security surface**, which is worse than either alone."
 *
 * That argument was sound and its premise no longer holds. The operator runs an
 * Amplify + Cognito deployment already, and the migration is what dissolves the
 * hardest open item: `web/lib/tenant.ts` records how a verified `custom:tenant`
 * claim replaces the RLS-scoped read that could not scope itself.
 *
 * **The cost the old argument named is real and is NOT paid off.** A self-hosted
 * stack with no Cognito pool now cannot sign anyone in at all: the local Postgres
 * holds no credential this application reads. That is a genuine loss, it is the
 * price of the item-1 fix, and it is stated here rather than discovered by
 * somebody running `podman compose up`.
 *
 * =========================================================================
 * WHAT REMAINS HERE, AND WHY IT IS NOT DEAD CODE
 * =========================================================================
 * Two exports, both with exactly one caller outside this lane, and both kept
 * because deleting them turns `tsc` red on a file this lane may not edit:
 *
 *   `sessionPool()` — `app/api/link/github/route.ts` opens a transaction on it.
 *   `auth()`        — the same route reads `session?.user?.id` from it.
 *
 * **THAT ROUTE'S SEMANTICS ARE NOW WRONG AND THIS FILE CANNOT FIX THEM.** It
 * deletes `accounts` and `sessions` rows keyed on an Auth.js `userId`; a Cognito
 * session creates neither, so it will delete **zero rows and report
 * `revoked: true`** with both counts at 0. Those counts exist precisely so the
 * answer is falsifiable — its own comment says "`revoked: true` with zero rows
 * deleted would mean the token was never there" — so the route reports the truth
 * about a table nothing writes any more. The honest fix is to delete the route
 * and its `ENDPOINTS` row, which is Lane I's file and this lane's report names it.
 *
 * NEVER LOGGED, NEVER COMMITTED. `COGNITO_CLIENT_SECRET` is read from the
 * environment in `cognito.ts` and appears in no file here.
 */

import { Pool } from "pg";

import { authorizeSession } from "./authorize";
import { verifySession, SESSION_COOKIE } from "./cognito";

/**
 * The connection pool, built ONCE and lazily.
 *
 * Lazy because this module is imported by every route, and a pool constructed at
 * import would open sockets in any process that merely typechecks or tests
 * against it. Once, because a pool per request is not a pool.
 *
 * **THE LAZINESS IS NOW LOAD-BEARING FOR THE BUILD, NOT ONLY FOR TESTS.** The
 * Auth.js configuration called this at module scope (`adapter:
 * PostgresAdapter(sessionPool())`), so `next build` collected page data for every
 * route that imported it and **refused without `DATABASE_URL`**. With Auth.js
 * gone nothing calls it until a request arrives, which is why the build's
 * required-variable list shrank. Do not reintroduce a module-scope call.
 *
 * `DATABASE_URL` unset is a REFUSAL, not an in-memory fallback — the same
 * direction as `STATE_BACKEND` refusing an unknown value rather than falling back
 * to `local`.
 */
let pool: Pool | null = null;

export function sessionPool(): Pool {
  if (pool === null) {
    const url = process.env.DATABASE_URL ?? "";
    if (!url.trim()) {
      throw new Error(
        "DATABASE_URL is not set, so there is no database to read. Refused " +
          "rather than falling back: an in-memory store answers every lookup " +
          "with 'nothing is there', which is indistinguishable from a row that " +
          "was never written, and the deployment would look healthy. Point it " +
          "at the same Postgres the queue and tenancy use -- see " +
          "infra/selfhost/docker-compose.yml.",
      );
    }
    pool = new Pool({ connectionString: url });
  }
  return pool;
}

/** The shape `next-auth`'s `auth()` returned, narrowed to what one caller reads. */
export interface CompatSession {
  user: { id: string; name: string };
}

/**
 * A COMPATIBILITY SHIM, and it is named one so nobody mistakes it for the seam.
 *
 * `app/api/link/github/route.ts` is not this lane's file and reads
 * `session?.user?.id`. Keeping this export is what lets that route keep
 * compiling; **it is not the way any new code should obtain an identity.**
 * `web/lib/session.ts`'s `currentIdentity()` is, and it is the only function that
 * applies the role and tenant checks — this one applies neither, deliberately,
 * because the caller only needs a stable subject to key a `DELETE` on.
 *
 * `id` is the Cognito `sub` rather than an Auth.js user id, so the `DELETE`s it
 * feeds match nothing. That is the honest outcome of removing the tables that
 * gave those ids meaning, and it is recorded in this file's header rather than
 * papered over by inventing a lookup.
 *
 * Reads the cookie itself rather than going through `session.ts` so the import
 * graph stays one-directional: `session.ts` imports this module's `sessionPool`
 * in no version of this file, and this module importing `session.ts` would make
 * the pair mutually dependent.
 */
export async function auth(): Promise<CompatSession | null> {
  try {
    const { cookies } = await import("next/headers");
    const jar = await cookies();
    const identity = await verifySession(jar.get(SESSION_COOKIE)?.value);
    const authorised = authorizeSession(identity, Date.now());
    if (!authorised.permitted) return null;
    return {
      user: { id: authorised.identity.sub, name: authorised.identity.login },
    };
  } catch {
    // Outside a request scope, or a verifier that could not run. Either way
    // nobody is authenticated, and fail-closed is the only correct answer on a
    // function whose result gates a delete.
    return null;
  }
}

// `allowedOrigins` lives in `web/lib/origins.ts`, re-exported here so callers
// have one import. It is a separate module because THIS one builds a `pg.Pool`,
// and a function that decides whether to refuse a mutation must be reachable by a
// test with no database. That separation predates Cognito and still holds.
export { allowedOrigins, originsFrom } from "./origins";
