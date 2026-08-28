-- THE WEB APPLICATION'S OWN TABLES. Lane I. Postgres.
--
-- Applied alongside `agentorg/db/schema.py`'s tenancy tables, in the SAME database.
-- Spec §11: "Sessions live in the Postgres the stack already needs for the queue and
-- tenancy, so it costs no new infrastructure." Not a second database, for the reason
-- the Compose file gives about the queue: two databases mean two facts that can
-- disagree with no transaction spanning the pair.
--
-- =============================================================================
-- WHY THESE ARE NOT IN `agentorg/db/schema.py`
-- =============================================================================
-- Two of the reasons are Lane B's design working as intended.
--
-- The four Auth.js tables below are AUTH.JS'S SCHEMA, not ours: `@auth/pg-adapter`
-- hardcodes the column names in its SQL (`sessionToken`, `providerAccountId`,
-- `emailVerified` -- camelCase and quoted, measured by reading its `index.js`). And
-- `Table.__post_init__` in `schema.py` requires every table to declare either a real
-- `tenant_column` or an explicit written `unscoped_reason`. Adding them there would
-- mean inventing a tenant column the adapter's SQL never filters on -- which is
-- precisely "a scope column that does not exist would render a guard comparing
-- against nothing, which admits every row", the refusal that file already carries.
--
-- The honest shape is: these tables are outside tenant scope, and the reason is
-- written here rather than in another lane's file.
--
-- =============================================================================
-- WHY A SESSION IS NOT TENANT-SCOPED, AND WHERE THE SCOPE COMES FROM INSTEAD
-- =============================================================================
-- A person is a GLOBAL identity, exactly as `app_user` is: "one person may hold
-- memberships in several organisations, so no single tenant owns the row". A session
-- identifies the PERSON; the TENANT is resolved from their membership, per request,
-- by joining `web_identity` -> `app_user` -> `membership`.
--
-- Putting a tenant on the session would make it a value the session CARRIES rather
-- than one the database ASSERTS -- so revoking somebody's membership would leave
-- their live session still scoped to the tenant they were removed from, for up to
-- thirty days, with nothing anywhere saying so.

-- ── AUTH.JS'S FOUR TABLES ────────────────────────────────────────────────────
--
-- Shapes taken from `@auth/pg-adapter`'s own SQL. Quoted camelCase identifiers are
-- REQUIRED, not a style choice: Postgres folds unquoted identifiers to lower case,
-- and the adapter's queries quote them, so an unquoted `sessionToken` column would
-- be created as `sessiontoken` and every session lookup would fail on a missing
-- column -- at sign-in, in a deployment, with the schema looking correct.

CREATE TABLE IF NOT EXISTS users (
  id            SERIAL PRIMARY KEY,
  name          VARCHAR(255),
  email         VARCHAR(255),
  "emailVerified" TIMESTAMPTZ,
  image         TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
  id                  SERIAL PRIMARY KEY,
  "userId"            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type                VARCHAR(255) NOT NULL,
  provider            VARCHAR(255) NOT NULL,
  "providerAccountId" VARCHAR(255) NOT NULL,
  -- THE GITHUB ACCESS TOKEN LANDS HERE. It carries the `repo` scope, so this column
  -- is the most sensitive thing in this schema: it can act on every repository the
  -- person can reach. Consequences, all deliberate:
  --   * `ON DELETE CASCADE` above means deleting the user deletes the grant, so
  --     revocation cannot leave an orphaned token nothing points at;
  --   * no index on it, so it never appears in a query plan an operator reads;
  --   * nothing in `web/app/api/**` returns it. A route that sent this to a browser
  --     would hand a repository-wide credential to client JavaScript.
  refresh_token       TEXT,
  access_token        TEXT,
  expires_at          BIGINT,
  id_token            TEXT,
  scope               TEXT,
  session_state       TEXT,
  token_type          TEXT,
  -- One account per provider identity. Without it a second sign-in through the same
  -- GitHub account creates a second row, and which token is used becomes whichever
  -- the query returned first.
  UNIQUE (provider, "providerAccountId")
);

CREATE TABLE IF NOT EXISTS sessions (
  id             SERIAL PRIMARY KEY,
  "userId"       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires        TIMESTAMPTZ NOT NULL,
  -- THE COOKIE VALUE. Unique because it is the lookup key, and `ON DELETE CASCADE`
  -- because "revoke this person" must end their live sessions in the same
  -- statement -- a JWT strategy could not offer that at all, which is why
  -- `authConfig` sets `strategy: "database"`.
  "sessionToken" VARCHAR(255) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS verification_token (
  identifier VARCHAR(255) NOT NULL,
  expires    TIMESTAMPTZ NOT NULL,
  token      VARCHAR(255) NOT NULL,
  PRIMARY KEY (identifier, token)
);

-- ── THE BRIDGE: an Auth.js user IS a tenancy user ────────────────────────────
--
-- `users.id` is a `SERIAL` integer chosen by Auth.js; `app_user.id` is a TEXT id
-- chosen by us. Neither can be made to be the other -- the adapter's SQL inserts
-- into `users` and returns its own id, and `app_user` is Lane B's table with its own
-- shape -- so the mapping is a table rather than a shared key.
--
-- ONE ROW PER AUTH.JS USER, and that is what `UNIQUE` enforces on both columns
-- independently: one Auth.js identity maps to exactly one tenancy user, and one
-- tenancy user is reachable from exactly one Auth.js identity. Without the second
-- constraint two GitHub accounts could both map to one `app_user`, and a decision
-- recorded against that user would name a person who may not have made it.
CREATE TABLE IF NOT EXISTS web_identity (
  -- Auth.js's user id, as an integer, because that is what it is.
  auth_user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  -- Lane B's `app_user.id`. NOT declared as a foreign key, deliberately: `app_user`
  -- is rendered by `agentorg/db/schema.py` and this file must apply whether or not
  -- that DDL has run yet, since the two are applied by different processes (the
  -- Python migration runner and this file). A REFERENCES clause naming a table that
  -- does not exist yet makes the whole script fail, and the ordering between two
  -- lanes' migrations is not something this file can assert.
  app_user_id  TEXT    NOT NULL UNIQUE,
  -- THE GITHUB LOGIN, which is what becomes `HumanDecision.by`. Stored rather than
  -- read from the token at decision time: a person may rename their GitHub account,
  -- and an audit record must say who it was when they clicked, not who that account
  -- belongs to now.
  github_login VARCHAR(255) NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (auth_user_id)
);

-- ── WHICH REPOSITORIES A TENANT HAS IN SCOPE (I2) ────────────────────────────
--
-- DELIBERATELY NOT CREATED HERE. `agentorg/db/schema.py` already declares
-- `repository` -- tenant-scoped, `UNIQUE (tenant_id, full_name)`, with the isolation
-- triggers and the Postgres RLS policy attached -- and `accessors.list_repositories`
-- / `add_repository` are the scoped way in.
--
-- A second table here would be a second answer to "is this repository in scope",
-- and I5's per-repository check reads that answer. Two tables that can disagree
-- about it means an approval permitted by one and refused by the other, with
-- nothing recording which was consulted.
