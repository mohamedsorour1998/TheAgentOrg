# ADR 001 — the tenancy database, and where isolation is actually enforced

**Status:** accepted, 2026-08-28. **Lane:** B. **Scope:** `agentorg/db/**`,
`agentorg/tenancy/**`.

---

## The decision

**One schema definition, two dialects. SQLite for the test suite, PostgreSQL for the
deployed stack.** The schema is Python data (`agentorg/db/schema.py`), rendered to DDL by
dialect, so a table cannot exist in one dialect and not the other — adding a table
produces both renderings or neither.

PostgreSQL is the committed deployment target: Lane F's Compose file brings up one
Postgres shared by the queue (`QUEUE_BACKEND=postgres`), these tenancy tables, and
Auth.js session storage. SQLite stays the tested path because `tests/conftest.py`'s six
autouse guards exist to make the whole suite runnable with no network and no
infrastructure, and that property is worth more than test/production symmetry. A suite
that needs a database server is a suite that stops being a gate.

---

## Where isolation is enforced, precisely

This is the part that is easy to overstate, and overstating it is the failure shape this
repository documents most often: a check that reads as stronger than it is. So, measured
rather than asserted:

| | SQLite (tested) | PostgreSQL (deployed) |
|---|---|---|
| cross-tenant **INSERT** | refused **at the database** — `BEFORE INSERT` trigger | refused at the database — RLS `WITH CHECK` |
| cross-tenant **UPDATE** | refused **at the database** — `BEFORE UPDATE` trigger, on both `OLD` and `NEW` | refused at the database — RLS |
| cross-tenant **DELETE** | refused **at the database** — `BEFORE DELETE` trigger | refused at the database — RLS |
| `REPLACE` / `UPSERT` | refused **at the database** — measured, see below | refused at the database — RLS |
| cross-tenant **SELECT** | refused **in the accessor layer only** | refused at the database — RLS `USING` |

**Writes are enforced at the database layer on both backends. Reads are enforced at the
database layer on Postgres only.** SQLite has no mechanism that constrains a `SELECT`
against a base table; there is no RLS and a trigger cannot fire on a read. Measured, with
two tenants' rows in one table and `current_tenant()` bound to `t1`:

```
3. view read as t1 : [('r1',)]
4. RAW table read as t1 (no guard on SELECT): [('r1',), ('r2',)]
```

So on the tested path, a forgotten `WHERE tenant_id = ?` in a read accessor **would**
leak. That is named here rather than papered over, and it is why the read accessors carry
an explicit tenant predicate that `tests/test_tenancy_leak.py` can break: the RED step for
the leak suite removes that predicate and the suite must fail. A scoped `VIEW` was
considered as a second read layer and **deliberately rejected** — it would have made the
accessor's predicate untestable, because removing it would leave the view quietly
covering the hole, and the suite would stay green over a real defect while looking like
defence in depth.

This answers the integrator's question as **option (a) for writes and option (b) for
reads**, stated in those terms so nobody has to infer it. Postgres RLS for reads is real
DDL emitted by this module and asserted structurally, but it is **not executed by the test
suite** — nothing here connects to Postgres. Treat "reads are enforced at the database"
as true of the deployed stack and unproven locally.

**What IS proven locally, and how.** The write half is not a structural claim about
emitted DDL — it is executed. Measured against a migrated in-memory database with two
tenants' rows and `t1` bound, every attempt going through raw SQL rather than an accessor,
so nothing in application code could be doing the refusing:

```
INSERT a run for t2              refused -> cross-tenant insert refused: run
UPDATE t2's run by id            refused -> cross-tenant update refused: run
DELETE t2's run                  refused -> cross-tenant delete refused: run
re-tenant own run into t2        refused -> cross-tenant update refused: run
REPLACE a run for t2             refused -> cross-tenant insert refused: run
insert claiming t1, NO tenant bound  refused -> cross-tenant insert refused: run
```

So the honest one-line summary, which is the sentence to quote if anybody asks: **writes
are refused by the database on both backends and this is executed in CI; reads are refused
by the database on Postgres only, and by the accessor layer on the tested path.** The
second clause is why `tests/test_tenancy_leak.py`'s RED step removes the accessor
predicate — with that predicate gone, 13 tests fail by name, which is the evidence that
the read defence is real rather than incidental.

### The two SQLite behaviours the design rests on, measured

**1. `!=` fails open; `IS NOT` does not.** The trigger's `WHEN` clause fires on a truthy
result, and SQL's three-valued logic makes `'t2' != NULL` evaluate to NULL, which is not
truthy. So a guard written the obvious way lets a write through **exactly when no tenant
is bound** — the case it most needs to catch:

```
     !=  current_tenant()=NULL, row claims t2 :  !!! INSERTED — guard did not fire
 IS NOT  current_tenant()=NULL, row claims t2 :  refused
     !=  acting t1, row claims t2             :  refused
 IS NOT  acting t1, row claims t2             :  refused
```

Both spellings refuse an ordinary mismatch, so the bug is invisible in the case anyone
would test by hand. Every trigger uses `IS NOT`, and a test pins the operator.

**2. Every write path reaches the triggers**, including the ones that do not look like
inserts:

```
INSERT OR REPLACE for t2     -> refused (insert refused)
UPSERT naming t2             -> refused (insert refused)
REPLACE for t2               -> refused (insert refused)
UPSERT onto t2's existing row -> refused (update refused)
```

Postgres's polarity is the reverse and lands in the same place: `current_setting(...,
true)` returns NULL when unset, `tenant_id = NULL` is NULL, and a policy admits a row only
on a true result — so an unbound tenant reads nothing and writes nothing. Fail-closed on
both, for opposite reasons. `FORCE ROW LEVEL SECURITY` is emitted alongside `ENABLE`
because without it the table **owner is exempt from its own policies**, which is a policy
that reads as protection and is not.

---

## Why a cross-tenant read raises instead of returning nothing

A read for another tenant's row raises `CrossTenantAccess`. It does not return `None`.

`None` would be untestable in the way that matters. "Assert the data is absent" passes
when isolation works, and equally when the row was never written, the table is empty, the
fixture is wrong, or the query is broken — it cannot fail for the right reason because it
cannot distinguish them. A raised refusal can only come from the code that refused.
Every breach attempt in the leak suite is therefore paired with a positive control
proving the rightful owner **can** read the same row: without that half, a refusal proves
only that nothing was there.

**The costed limitation:** distinguishing "not yours" from "does not exist" is an
existence oracle — a caller learns that an id it named exists under some other tenant.
The ids are UUIDs and run ids, unguessable in practice, so the oracle yields nothing an
attacker could enumerate; and the refusal carries no foreign data, only the fact that the
resource is outside the caller's scope, which a test pins. Removing the oracle entirely
means collapsing both cases into one exception, which costs the leak suite its
discriminator. That trade is made deliberately in favour of the suite.

---

## Two tables are not tenant-scoped, on purpose

`organisation` is self-scoped: a row **is** a tenant, so its tenant column is its own
primary key and a tenant reads exactly its own row.

`app_user` is a global identity — one person may belong to several organisations, so a
`tenant_id` on that table would be a lie. It is therefore **not reachable from a tenant
scope at all**: there is no `get_user` accessor. Users are readable only through
`membership`, joined and filtered to the scope, which closes the enumeration surface
without pretending a global table is scoped. Declaring a table unscoped requires writing
down why — `Table.unscoped_reason` is validated non-empty — so the next table cannot
become unscoped by omission.

---

## Secrets: the cipher is a seam, and the local one is labelled

`cryptography` is **not in the declared dependency closure** — measured, because PyJWT
requires it only under an extra, and CI installs `.[dev]` and nothing else:

```
cryptography in declared closure: False
pyjwt in declared closure: True
```

So a `cryptography` import would work in this venv and fail in CI, and this lane does not
own `pyproject.toml`. The cipher is therefore a seam with a stdlib default:
encrypt-then-MAC over an HMAC-SHA256 keystream, per-record random nonce, scrypt-derived
keys, verified with `compare_digest`. Every row records **which cipher wrote it**, the way
`SecurityResult.scan_provenance` records which scanner mode produced a verdict — so a KMS
migration is visible in the data and a downgrade is detectable rather than inferred.

The stdlib construction is adequate for a demo and is **not** an AES-GCM replacement. The
deployed path should bind the seam to KMS (boto3 *is* declared). That is a further
hardening step, stated as one rather than implied to be done.

The master key is read from the environment, is never logged, never rendered by `repr`,
and a blank key **raises** rather than defaulting — a default master key is a shared
master key.

---

## Budgets fail closed

A tenant with **no** budget row is refused, not admitted. "Nobody configured a ceiling"
must not read as "unlimited", for the same reason `RunState.ci_status_measured` treats
`""` as "nobody measured" rather than as `unknown`. Unlimited is a column, set
explicitly, and tenant zero gets it explicitly at migration.

Money is stored in **integer cents**. A float ceiling compared against a float spend is a
bug with a currency symbol in front of it.

---

## Tenant zero keeps its `""`

`RunState.tenant_id` defaults to `""`, which is what every run on disk already carries and
what the current deployment stays as. Tenant zero is a real row with a real non-blank id
(`tenant-zero`), and one function translates: `""` on a `RunState` means tenant zero in
the database. Nothing rewrites the runs, and `state.py` is untouched. A blank tenant is
refused everywhere else, so the translation is the only place `""` has meaning.
