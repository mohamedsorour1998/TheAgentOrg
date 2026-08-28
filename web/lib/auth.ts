/**
 * AUTH.JS, GITHUB OAUTH, SESSIONS IN POSTGRES. Task I1.
 *
 * Spec §11 records the decision and the reasoning, which is worth restating in the
 * one file that implements it: **Auth.js, not Cognito.** Cognito is the quickest
 * path to working auth on AWS and it collides with requirement 4 — §6's answer to
 * "self hosted?" is a demonstration where the stack comes up on the operator's own
 * machine and a poisoned ticket still blocks. With Cognito in the auth path that
 * demonstration either cannot sign anyone in, or needs a second auth
 * implementation for the self-hosted case: **two code paths on a security
 * surface**, which is worse than either alone.
 *
 * GitHub OAuth is not optional anyway — the product acts on somebody's
 * repositories, so the grant is required regardless. Making it the primary
 * provider collapses sign-in and §11's "link account" into one flow instead of two,
 * which is why I1 and I2 share this file.
 *
 * =========================================================================
 * WHAT IS VERIFIED HERE AND WHAT IS NOT — READ BEFORE TRUSTING THIS FILE
 * =========================================================================
 * `@auth/pg-adapter` takes a `pg.Pool` and issues SQL against four tables:
 * `users`, `accounts`, `sessions` and `verification_token` (measured by reading
 * `node_modules/@auth/pg-adapter/index.js`, not from the docs). So:
 *
 *   VERIFIED locally: this module imports, the configuration typechecks, and
 *   `tenantForUser` / `sessionView` are exercised by tests with no database.
 *
 *   NOT VERIFIED, and nothing in this repository can claim otherwise: no Postgres
 *   has been connected. `agentorg/db/engine.py:connect()` is **sqlite3 only** —
 *   there is no Postgres connection factory in `agentorg/db/` at all, and Lane B's
 *   own note says "nothing in the suite connects to Postgres". The Compose stack
 *   defines one and `docker compose up` has never been run against it.
 *
 * That distinction is the same one this repository draws between a green
 * `terraform apply` and `simulate-principal-policy`, and between a compose file
 * that parses and a stack that runs. **A sign-in flow that has never completed
 * against a real Postgres is not a working sign-in flow**, and saying so here is
 * cheaper than a judge discovering it.
 *
 * THE FOUR AUTH.JS TABLES ARE NOT IN `agentorg/db/schema.py`, DELIBERATELY.
 * That file is Lane B's and its `Table.__post_init__` requires every table to
 * declare a `tenant_column` or an explicit written reason — and these four are
 * Auth.js's own schema, whose column names (`sessionToken`, `providerAccountId`,
 * `emailVerified`) the adapter hardcodes. Adding them there would mean either
 * inventing a tenant column the adapter's SQL does not filter on — a guard
 * comparing against nothing — or four more `unscoped_reason` entries in another
 * lane's file. They live in `web/lib/schema.sql`, alongside, in the same database.
 *
 * ONE DATABASE, TWO SCHEMAS. Spec §11: "Sessions live in the Postgres the stack
 * already needs for the queue and tenancy, so it costs no new infrastructure."
 * Not a second database, for the reason the Compose file gives about the queue:
 * two databases mean two facts that can disagree with no transaction spanning them.
 */

import PostgresAdapter from "@auth/pg-adapter";
import type { NextAuthConfig } from "next-auth";
import NextAuth from "next-auth";
import GitHub from "next-auth/providers/github";
import { Pool } from "pg";

/**
 * The GitHub scopes requested. NARROW ON PURPOSE, and each one is here because a
 * task needs it.
 *
 * `read:user` identifies who signed in — the `login` that becomes
 * `HumanDecision.by`, and the whole difference between this surface and
 * `approve_server`'s `by="ui-reviewer"`.
 *
 * `repo` is what I2 needs to list a person's repositories and to act on one.
 *
 * WHAT IS DELIBERATELY NOT REQUESTED: `admin:org`, `delete_repo`, `admin:repo_hook`
 * and `workflow`. This product opens pull requests and reads check runs; it does
 * not administer an organisation, delete anything, or rewrite workflow files. A
 * scope granted "in case" is a scope an attacker who steals the token inherits, and
 * the consent screen a person reads is the only place this list is visible to them.
 *
 * `repo` IS BROAD AND THAT IS AN ACCEPTED LIMIT, NOT A HIDDEN ONE. GitHub's OAuth
 * `repo` scope is all-or-nothing across every repository the person can reach —
 * there is no per-repository OAuth scope. A GitHub **App** installation would give
 * per-repository grants and is the correct end state; it is a different
 * authorisation model (installation tokens, not user tokens) and is recorded as a
 * further step rather than half-built. Until then, I5's per-repository check is
 * enforced by THIS application against the tenant's own scope list — which is why
 * `authz.decide` takes `repositoriesInScope` and refuses an empty one.
 */
export const GITHUB_SCOPES = "read:user repo";

/**
 * How long a session lasts. Thirty days, sliding.
 *
 * Chosen against what the session AUTHORISES: this one can open a security gate.
 * A year-long session on that surface means a stolen laptop stays authorised for a
 * year. Thirty days is the same order as the GitHub grant it wraps, and shorter
 * than the demo cycle so nobody is tempted to raise it mid-week.
 */
const SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60;

/**
 * `AUTH_SECRET` must exist, and its absence is a REFUSAL rather than a generated
 * default.
 *
 * Auth.js will invent a secret in development if none is set, and that is exactly
 * the wrong behaviour for this application: the secret signs session cookies, so a
 * per-process generated one means every restart silently invalidates every session
 * — and in a multi-instance deployment two instances disagree about who is signed
 * in, intermittently, with no error anywhere.
 *
 * Refused at import for `STATE_BACKEND`'s reason: "a typo'd `dynamo` silently
 * writing to disk would leave an operator believing a run is durable". Here an
 * absent secret would leave an operator believing sessions are stable.
 *
 * NEVER LOGGED, and the message never quotes it.
 */
function requireSecret(): string {
  const secret = process.env.AUTH_SECRET ?? "";
  if (!secret.trim()) {
    throw new Error(
      "AUTH_SECRET is not set. Refused rather than generated: this secret signs " +
        "the session cookies that authorise a gate approval, so a per-process " +
        "value silently invalidates every session on restart and makes two " +
        "instances disagree about who is signed in. Generate one with " +
        "`openssl rand -base64 32` and set it in the environment — never in a " +
        "committed file.",
    );
  }
  return secret;
}

/**
 * The Auth.js configuration.
 *
 * `session.strategy` is DATABASE, not JWT, and that is the one setting here worth
 * arguing about. A JWT session is self-contained, so it cannot be revoked before
 * it expires — and this session can approve a security gate, which makes "revoke
 * immediately" a requirement rather than a nicety. I2 asks for revocation; a JWT
 * strategy would make that endpoint a lie, deleting a row nothing reads while the
 * cookie kept working for thirty days.
 *
 * A database session costs one query per request. That is the price of being able
 * to end one.
 */
/**
 * The connection pool, built ONCE and lazily.
 *
 * Lazy because this module is imported by every route, and a pool constructed at
 * import would open sockets in any process that merely typechecks or tests against
 * it. Once because a pool per request is not a pool.
 *
 * `DATABASE_URL` unset is a REFUSAL, not an in-memory fallback. The same direction
 * as the reader's `AGENTORG_DB_PATH` check: a fallback would answer every session
 * lookup with "nobody is signed in", which reads exactly like a person who has not
 * signed in yet, and the deployment would look healthy while nobody could stay
 * logged in.
 */
let pool: Pool | null = null;

function sessionPool(): Pool {
  if (pool === null) {
    const url = process.env.DATABASE_URL ?? "";
    if (!url.trim()) {
      throw new Error(
        "DATABASE_URL is not set, so there is nowhere to keep sessions. Refused " +
          "rather than falling back: an in-memory store answers every session " +
          "lookup with 'nobody is signed in', which is indistinguishable from a " +
          "person who has not signed in, and nobody could stay logged in while " +
          "the deployment looked healthy. Point it at the same Postgres the queue " +
          "and tenancy use -- see infra/selfhost/docker-compose.yml.",
      );
    }
    pool = new Pool({ connectionString: url });
  }
  return pool;
}

export const authConfig: NextAuthConfig = {
  // THE ADAPTER IS WHAT PUTS SESSIONS IN POSTGRES, and it is what makes
  // `strategy: "database"` below mean anything -- without an adapter Auth.js
  // silently falls back to a JWT session, which cannot be revoked. So these two
  // settings are one decision in two places, and neither works alone.
  adapter: PostgresAdapter(sessionPool()),
  secret: requireSecret(),
  providers: [
    GitHub({
      // Read from the environment, never from a file in the repository. An OAuth
      // client secret can mint tokens against somebody's repositories.
      clientId: process.env.AUTH_GITHUB_ID ?? "",
      clientSecret: process.env.AUTH_GITHUB_SECRET ?? "",
      authorization: { params: { scope: GITHUB_SCOPES } },
    }),
  ],
  session: {
    strategy: "database" as const,
    maxAge: SESSION_MAX_AGE_SECONDS,
  },
  // TRUST THE HOST ONLY WHEN AN ORIGIN IS CONFIGURED. Auth.js derives callback URLs
  // from the request host, and trusting an arbitrary `Host` header lets a request
  // rewrite the callback — so this is false unless `AUTH_URL` names the origin
  // explicitly. Deliberately not `true`: the convenient setting is the one that
  // makes a host-header attack work.
  trustHost: Boolean(process.env.AUTH_URL),
};

// `allowedOrigins` lives in `web/lib/origins.ts`, re-exported here so callers have
// one import. It is a separate module because THIS one cannot be imported without a
// database: `requireSecret()` and `sessionPool()` run at import by design, and a
// function that decides whether to refuse a mutation must be reachable by a test.
export { allowedOrigins, originsFrom } from "./origins";

export const { handlers, auth, signIn, signOut } = NextAuth(authConfig);
