"""K5: machine-to-machine auth. A bearer key that resolves to a tenant, or a 401.

OWNER: Lane K.

=========================================================================
WHAT A KEY FROM THIS MODULE CANNOT DO: APPROVE OR RESUME A HUMAN GATE.
=========================================================================
Read `agentorg/api/__init__.py` first for the full argument. The short form: a
machine credential that could approve a gate defeats the gate for exactly the
population gates exist to exclude, and the run's `HumanDecision.by` would then
name a service account -- a record that reads as a human decision. There is no
scope in `SCOPES` that grants it, and no route in the API maps to `gates.resume`
or `queue.resume` at all, so the absence is structural rather than a scope check
somebody could widen.

If an approval route is ever wanted, the credential has to carry a HUMAN
identity, which is a different scheme from these tokens -- an operator logs in,
a machine does not. `approve_server.py`'s docstring already says the same thing
about itself ("a real deployment needs an operator identity on each decision...
it belongs in the codebase's auth seam rather than invented here"). This module
is that seam for MACHINES only, and saying so is the point.

=========================================================================
AN EMPTY KEY STORE IS A REFUSAL, NOT AN EXEMPTION.
=========================================================================
The store starts empty and every authenticated route then answers 401. That
direction is the whole design and it is the same one `budgets.check` takes for a
tenant with no budget row, `config.STATE_BACKEND` takes for an unknown value, and
`RunState.ci_status_measured` takes for a blank: **"nobody configured this" and
"this caller may do anything" are different facts and must not share a
representation.**

Written the other way -- no keys means open -- a deployment that failed to
provision a key would accept every anonymous caller, every request would succeed,
and the first signal would be somebody else's run in your tenant. That failure is
invisible from the inside, which is why it is refused here rather than documented.

HOW A KEY IS STORED, AND WHY IT IS NOT REVERSIBLE
=================================================
`issue_key` returns the secret ONCE and stores only a hash of it. The store
therefore cannot answer "what is tenant X's key", by construction -- so a store
dump, a log line, or a `GET /v1/keys` route somebody adds later cannot leak a
live credential. That is `schema.SECRET`'s decision one layer up ("no plaintext
column exists, so there is no column a careless write could put a token in"),
applied to our own credentials rather than the customer's.

`hashlib.scrypt`, stdlib, with a per-key random salt. **Deliberately not
`cryptography`** -- `tenancy/crypto.py` records why at length: that package is
absent from the declared dependency closure (PyJWT requires it only under an
extra), so an import works locally and fails in CI. And deliberately not a bare
`sha256`: an API key is a secret a person may reuse, and a fast hash over a
guessable one is a dictionary attack against the store.

MEASURED on this machine, because a KDF's cost is the whole point of choosing one
and a number nobody measured is a guess:

    n=2**14 (16384)  ->  27.7 ms per verification
    n=2**13 ( 8192)  ->  11.6 ms

`SCRYPT_N = 2**14` is the choice. 28 ms is imperceptible on a control-plane call
that is about to enqueue a job, and it is four orders of magnitude slower than
sha256 for anybody grinding the store.

`compare_digest`, NEVER `==`, ON THE DIGEST COMPARISON
======================================================
Same reason `infra/ingress/handler.py` gives as its trap 4: `==` returns early at
the first differing byte and leaks its position through timing, "which is enough
to forge a signature one byte at a time". Both operands are bytes before the
compare, so a non-ASCII secret cannot turn a 401 into a 500 through `TypeError`.

THE KEY ID IS PUBLIC AND THE SECRET IS NOT, AND THAT SPLIT IS LOAD-BEARING
=========================================================================
A key is `agtk_<key_id>_<secret>`. The store is looked up by `key_id` alone, so a
verification is one hash rather than one hash per stored key -- and a caller
presenting an unknown `key_id` gets the same 401, after the same scrypt work, as
one presenting a known id with a wrong secret. Without that the response time
would answer "does this key id exist?", which is the timing side channel one
level up from the digest compare.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

from .errors import Unauthenticated

# The prefix every key carries. Grepping a leaked log or a pasted config for
# `agtk_` finds our credentials specifically -- the reason GitHub prefixes
# `ghp_`/`github_pat_`, and CLAUDE.md's own token-in-a-tfplan incident was found
# by exactly such a grep (`github_pat_[A-Za-z0-9_]{20,}`).
KEY_PREFIX = "agtk"

# The separator between prefix, id and secret. `_` rather than `.` because a key
# ends up in an `Authorization` header, a shell variable and a URL-safe context,
# and `_` is safe in all three.
_SEP = "_"

# scrypt parameters. `n` measured above; `r` and `p` are the values RFC 7914 uses
# in its own worked example and that hashlib's documentation carries.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32

# Bytes of entropy in each half. 16 bytes of key id is enough that ids do not
# collide; 32 bytes of secret is the part that has to resist guessing.
_KEY_ID_BYTES = 16
_SECRET_BYTES = 32

# The scopes a machine credential can hold. DELIBERATELY SHORT, and deliberately
# missing the one somebody will look for.
#
# There is no `gates:approve`, no `runs:resume` and no `runs:promote`. Adding one
# would not be enough to make an approval work -- no route reaches `gates.resume`
# -- but it would read as though the capability existed and was merely
# unassigned, which is worse than its absence: the next person grants it and goes
# looking for the route that must be broken.
SCOPE_RUNS_WRITE = "runs:write"    # submit and cancel
SCOPE_RUNS_READ = "runs:read"      # status
SCOPE_CONFIG_WRITE = "config:write"
SCOPE_CONFIG_READ = "config:read"
SCOPES = (SCOPE_RUNS_WRITE, SCOPE_RUNS_READ, SCOPE_CONFIG_WRITE, SCOPE_CONFIG_READ)


@dataclass(frozen=True)
class Credential:
    """A verified caller: which tenant, which scopes, which key.

    Frozen, like `gates.StateRef` and `tenancy.TenantScope`, so a handler cannot
    widen its own scopes after the check. `tenant_id` is a real tenant id and
    never `""` -- `tenancy.scope_for` refuses a blank, and the translation of the
    single-tenant marker happens at issue time (see `issue_key`), not here.
    """

    key_id: str
    tenant_id: str
    scopes: frozenset[str]

    def require(self, scope: str) -> None:
        """Raise unless this credential carries `scope`.

        Raises `Unauthenticated` rather than `Forbidden`, deliberately: a token
        without the scope has not established a right to be told what it is
        missing, and the alternative message ("you need runs:write") is a map of
        the API for anybody holding a low-privilege key.
        """
        if scope not in self.scopes:
            raise Unauthenticated()


@dataclass
class _StoredKey:
    """What the store holds. A hash and a salt -- never the secret."""

    key_id: str
    tenant_id: str
    scopes: frozenset[str]
    salt: bytes
    digest: bytes
    revoked: bool = False


@dataclass
class InMemoryKeyStore:
    """Keys for one process. The tested path, and the only implemented one.

    IN-PROCESS ON PURPOSE, and the honest limit is stated rather than implied:
    keys do not survive a restart and are not shared between processes, so this
    is not the store a multi-node deployment uses. The durable version is a row
    in `agentorg/db/` -- `schema.SECRET` is already the right shape and already
    tenant-scoped -- and that is Lane B's file, so it is named here rather than
    reached into.

    What this DOES do correctly is refuse. Every property the durable store must
    have is enforced here and tested here: an empty store 401s, a revoked key
    401s, an unknown key id costs the same scrypt work as a wrong secret, and
    nothing stored can be turned back into a credential.

    Deliberately mirrors `queue._BACKENDS` in shape: module state with an
    explicit setter and a `reset`, because a store a caller could construct
    per-request would let two callers disagree about who is authorised.
    """

    keys: dict[str, _StoredKey] = field(default_factory=dict)

    def put(self, key: _StoredKey) -> None:
        self.keys[key.key_id] = key

    def get(self, key_id: str) -> _StoredKey | None:
        return self.keys.get(key_id)

    def revoke(self, key_id: str) -> bool:
        """Mark a key unusable. Returns whether it existed.

        The row is KEPT rather than deleted, so a later question about which key
        made a call still has an answer. Same instinct as `modules/state`'s IAM
        omitting `DeleteItem`: an audit trail that can be pruned by the thing it
        audits is not one.
        """
        stored = self.keys.get(key_id)
        if stored is None:
            return False
        stored.revoked = True
        return True

    def clear(self) -> None:
        """Drop every key. For tests, and for tests only -- see queue.reset."""
        self.keys.clear()


_STORE = InMemoryKeyStore()


def key_store() -> InMemoryKeyStore:
    """The process's key store.

    A function rather than the bare name, for the reason CLAUDE.md gives about
    reading knobs through the module: a caller holding `from .auth import _STORE`
    binds the object at import and would not see a test's replacement.
    """
    return _STORE


def set_key_store(store: InMemoryKeyStore) -> None:
    """Replace the process's key store. The single seam a test substitutes."""
    global _STORE
    _STORE = store


def hash_secret(secret: str, salt: bytes) -> bytes:
    """The stored digest for a secret. The ONLY place scrypt is called.

    One call site so the parameters cannot drift between issuing and verifying --
    a mismatch there makes every key fail to verify, which presents as "the
    secret is wrong" and sends the next person to reissue a credential that was
    always correct. Exactly the failure `handler._webhook_secret` describes for a
    misspelled JSON key.
    """
    return hashlib.scrypt(
        secret.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )


def issue_key(tenant_id: str, scopes: object = SCOPES) -> tuple[str, str]:
    """Mint a key for a tenant. Returns `(key_id, the full key)` ONCE.

    The full key is never recoverable afterwards -- only its hash is stored. A
    caller that loses it issues another and revokes the first, which is what
    every credential system worth copying does, and it means no code path in this
    repository can print a live key it read back from somewhere.

    A BLANK TENANT IS REFUSED, matching `tenancy.scope_for`: `""` is the
    single-tenant marker every pre-tenancy `RunState` carries, and a credential
    bound to it would scope to a tenant column nobody owns. The translation is
    `tenancy.tenant_zero.for_run_state`, and the caller does it before arriving
    here -- done here instead, this module would be a second place that knows
    what a blank tenant means.

    An UNKNOWN SCOPE is refused rather than dropped. Silently discarding one
    issues a key that verifies and then cannot do what its holder was told it
    could, and the failure surfaces as a 401 on an unrelated route.
    """
    if not tenant_id or not tenant_id.strip():
        raise ValueError(
            "tenant_id may not be blank. \"\" is the single-tenant marker every "
            "pre-tenancy RunState carries; translate it with "
            "tenancy.tenant_zero.for_run_state() before issuing a key. A "
            "credential bound to a blank tenant scopes to a row nobody owns."
        )
    requested = frozenset(scopes)
    unknown = sorted(requested - set(SCOPES))
    if unknown:
        raise ValueError(
            f"unknown scope(s) {', '.join(unknown)}; this API grants only "
            f"{', '.join(SCOPES)}. Refused rather than dropped: a key issued "
            f"with a scope that was silently discarded verifies and then fails "
            f"on the route its holder was told it could reach. Note there is no "
            f"gate-approval scope and adding one would not create the "
            f"capability -- no route reaches gates.resume."
        )

    key_id = secrets.token_hex(_KEY_ID_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    salt = secrets.token_bytes(16)
    key_store().put(
        _StoredKey(
            key_id=key_id,
            tenant_id=tenant_id,
            scopes=requested,
            salt=salt,
            digest=hash_secret(secret, salt),
        )
    )
    return key_id, f"{KEY_PREFIX}{_SEP}{key_id}{_SEP}{secret}"


def parse_bearer(header: str | None) -> tuple[str, str]:
    """Split an `Authorization` header into `(key_id, secret)`, or raise.

    Accepts `Bearer <key>` case-insensitively on the scheme, because HTTP
    schemes are case-insensitive by specification and a caller sending `bearer`
    is not an attacker. The KEY itself is compared exactly.

    Every malformed shape raises the same `Unauthenticated`. A message
    distinguishing "no header" from "wrong scheme" from "not our prefix" would
    let a caller probe the format, and the format is half of a credential.

    SPLIT WITH `maxsplit=2`, AND THAT IS A MEASURED FIX RATHER THAN CAUTION.
    `secrets.token_urlsafe` emits base64url, whose alphabet CONTAINS `_` -- so a
    plain `key.split("_")` returned four or five pieces for a legitimately issued
    key and refused it. Found by issuing a key and resolving it in the same
    breath; a test that only ever asserted malformed keys are refused would have
    passed against this, because refusing everything satisfies it.

    `key_id` is `token_hex`, so it never contains the separator and the second
    field is unambiguous. The secret is whatever remains, compared exactly.
    """
    if not header:
        raise Unauthenticated()
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise Unauthenticated()
    key = parts[1].strip()
    pieces = key.split(_SEP, 2)
    if len(pieces) != 3 or pieces[0] != KEY_PREFIX or not pieces[1] or not pieces[2]:
        raise Unauthenticated()
    return pieces[1], pieces[2]


def resolve(header: str | None) -> Credential:
    """Verify an `Authorization` header and return who is calling.

    THE ONLY WAY INTO A TENANT SCOPE FROM AN HTTP REQUEST. Every authenticated
    route calls this and uses the `tenant_id` it returns; no route reads a tenant
    from the body or the path, which is what keeps the API from becoming a way
    around `tenancy`'s accessors. `test_no_route_takes_a_tenant_from_the_request`
    asserts it structurally.

    AN UNKNOWN KEY ID COSTS THE SAME WORK AS A WRONG SECRET. The scrypt call
    happens either way, against a throwaway salt when the id is unknown, so the
    response time does not answer "does this key exist?". That is the digest
    compare's timing argument one level up, and skipping it is the natural way to
    write this function -- an early `return` on a missing id reads as correct
    code and is a side channel.
    """
    key_id, secret = parse_bearer(header)
    stored = key_store().get(key_id)

    if stored is None:
        # Same work, discarded. `secrets.token_bytes` rather than a constant salt
        # so this branch cannot be told apart by a cache-timing argument either.
        hash_secret(secret, secrets.token_bytes(16))
        raise Unauthenticated()

    candidate = hash_secret(secret, stored.salt)
    if not hmac.compare_digest(candidate, stored.digest):
        raise Unauthenticated()

    # REVOKED IS CHECKED AFTER THE DIGEST, on purpose. Checked before, a revoked
    # key would 401 faster than a live one with a wrong secret, so the timing
    # would report that the key id is real. The order costs one hash and closes
    # that.
    if stored.revoked:
        raise Unauthenticated()

    return Credential(
        key_id=stored.key_id,
        tenant_id=stored.tenant_id,
        scopes=stored.scopes,
    )
