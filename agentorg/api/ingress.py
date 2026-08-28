"""K4: webhook ingress, generalised. The HMAC Lambda becomes one entry point.

OWNER: Lane K.

WHAT WAS ALREADY TRUE, AND WHAT THIS ADDS
========================================
`infra/ingress/handler.py` is a GitHub-shaped webhook verifier and it is correct:
CLAUDE.md records its ordering as the security boundary, "the HMAC check is step 5,
and everything that costs money or mutates anything is after it". It stays exactly
where it is and keeps doing exactly that -- it is the deployed path, it is under
`infra/` on purpose (so `test_requirements_covers_every_third_party_import_in_the_package`
does not walk its `boto3` import into five agent images), and nothing here touches
it.

What this module adds is the SECOND and THIRD providers. The Lambda hardcodes
GitHub's header names and GitHub's `sha256=` prefix, so a GitLab or a generic CI
caller has no entry point at all today. Generalising means naming the parts that
differ per provider and keeping the parts that must not.

    provider     signature header          prefix       digest
    github       X-Hub-Signature-256       "sha256="    HMAC-SHA256 hex
    gitlab       X-Gitlab-Token            ""           the token itself
    generic      X-Agentorg-Signature      "sha256="    HMAC-SHA256 hex

**GitLab is the interesting row and it is the reason this is a table rather than a
parameter.** GitLab does not sign anything: it sends the shared secret verbatim in
`X-Gitlab-Token`. So its verification is a constant-time compare of a token, not a
digest over a body -- and a design that assumed "every provider signs the body"
would either reject GitLab entirely or, far worse, compare its token against a
computed digest and refuse every delivery while looking like a signature bug. That
is the failure mode `handler.py` names for a misspelled secret key: "the symptom is
every delivery 401ing -- which reads as a wrong secret and sends the next person to
rotate a secret that was always correct."

`signs_body=False` therefore records a PROPERTY OF THE PROVIDER, and
`verify_delivery` branches on it. It is not a convenience.

WHAT DOES NOT VARY, BECAUSE IT IS THE SECURITY BOUNDARY
======================================================
Four things are the same for every provider and are not parameters:

  * **The raw body.** Nothing between reading it and `hmac.new` touches the bytes.
    A `json.dumps(json.loads(body))` round-trip renormalises whitespace and key
    order and 401s every delivery. `verify_delivery` takes `bytes` and has no
    string overload, so a caller cannot pass a re-serialised body by accident.
  * **`compare_digest`, never `==`.** `==` returns early at the first differing
    byte and leaks its position through timing. Both operands are encoded to
    bytes first, because `compare_digest` raises `TypeError` on a str with
    non-ASCII -- a hostile header of `sha256=é` would become a 500 instead of a
    401.
  * **A blank or whitespace-only secret is refused.** `handler._webhook_secret`
    catches this and says why: such a key "is not empty, so the HMAC succeeds and
    every delivery merely 401s -- but it is a 1-3 byte key an attacker can guess
    outright, which is the same universal-forgery hazard as an empty one".
  * **Verification happens before anything else.** This module's whole surface is
    one function that returns None or raises. It cannot enqueue, cannot read a
    tenant's data and cannot spend money, so "verify first" is enforced by the
    module having no other capability rather than by the order of its statements.

WHY THE PROVIDER'S NAME COMES FROM THE PATH AND NOT FROM A HEADER
================================================================
`POST /v1/ingress/<provider>`. Taken from a header, a caller could choose which
verification applies to their delivery -- and they would choose the cheapest,
which is GitLab's token compare against a secret they do not have, or a provider
this table does not know. An unknown provider is a 404 and the secret is never
fetched, which is `handler.py`'s step ordering: the reject paths that precede the
secret fetch exist so "an anonymous caller must not be able to drive
GetSecretValue calls against a public endpoint".

THIS MODULE DOES NOT START A RUN, AND THAT IS DELIBERATE FOR K4
==============================================================
It verifies and hands back the parsed payload. `api/server.py` then does what the
EventBridge rule does today -- decide whether this event is one that starts a run
at all. Splitting it that way keeps the property CLAUDE.md credits the rule with:
filtering at the bus rather than in the handler means "every Issues delivery is
recorded but only an opened issue starts a run", and filtering inside the verifier
"would make 'we never saw it' and 'we saw it and ignored it' indistinguishable".
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from .errors import BadRequest, NotFound, Unauthenticated


@dataclass(frozen=True)
class IngressProvider:
    """One webhook source: which header carries the proof, and what shape it is.

    Frozen, so a request handler cannot rewrite a provider's verification while
    serving a delivery.
    """

    name: str
    signature_header: str
    prefix: str
    signs_body: bool
    event_header: str
    delivery_header: str

    def __post_init__(self) -> None:
        # A provider that signs nothing AND declares a prefix is a contradiction:
        # the prefix exists to introduce a digest. Both set means two readings of
        # the same header and nothing records which was used -- the refusal
        # `scoring.ScannerScoring.__post_init__` makes for a table and a constant
        # both being set, for the same reason.
        if not self.signs_body and self.prefix:
            raise ValueError(
                f"{self.name}: signs_body is False but a prefix is declared. A "
                f"prefix introduces a digest, and a provider that sends a bare "
                f"token has none -- two readings of one header with nothing "
                f"recording which was used."
            )
        if not self.signature_header:
            raise ValueError(
                f"{self.name}: no signature header. A provider with nothing to "
                f"verify is an unauthenticated entry point, which is the one "
                f"thing this module exists to prevent."
            )


# THE PROVIDERS. GitHub's row must match `infra/ingress/handler.py`'s constants,
# and `test_the_github_provider_matches_the_deployed_lambda` asserts that by
# LOADING that file and comparing -- not by restating its values here, which would
# be a second declaration of the header the deployed path depends on. Two copies
# keep agreeing while one moves.
PROVIDERS: dict[str, IngressProvider] = {
    "github": IngressProvider(
        name="github",
        signature_header="x-hub-signature-256",
        prefix="sha256=",
        signs_body=True,
        event_header="x-github-event",
        delivery_header="x-github-delivery",
    ),
    "gitlab": IngressProvider(
        name="gitlab",
        # GitLab sends the SHARED SECRET ITSELF, not a signature. See the module
        # docstring: this is why `signs_body` exists as a field.
        signature_header="x-gitlab-token",
        prefix="",
        signs_body=False,
        event_header="x-gitlab-event",
        delivery_header="x-gitlab-event-uuid",
    ),
    "generic": IngressProvider(
        name="generic",
        # For a CI caller that has no webhook product behind it -- a Jenkins job,
        # a script. Our own header name, our own scheme, so nothing has to be
        # reverse-engineered from a vendor's docs.
        signature_header="x-agentorg-signature",
        prefix="sha256=",
        signs_body=True,
        event_header="x-agentorg-event",
        delivery_header="x-agentorg-delivery",
    ),
}


def provider_for(name: str) -> IngressProvider:
    """The provider by name, or a 404.

    404 AND NOT 401. An unknown provider is not a failed authentication -- saying
    401 would tell a caller that `/v1/ingress/bitbucket` exists and rejected them,
    and it would put a signature failure in the log for what is actually a typo
    in a URL. `handler.py` makes the same distinction the other way round, using
    405 for a non-POST "so a probe never reads as a signature failure in the logs".
    """
    provider = PROVIDERS.get(name.lower().strip())
    if provider is None:
        raise NotFound(
            f"no ingress provider named {name!r}; this service accepts "
            f"{', '.join(sorted(PROVIDERS))}"
        )
    return provider


def header_value(headers: dict, name: str) -> str:
    """Read a header case-insensitively.

    `handler._header`'s trap 3, restated as behaviour rather than as a comment:
    Function URLs deliver lower-cased names, and "relying on that silently means
    the same code 401s behind anything that preserves case (API Gateway REST, ALB,
    a local test harness)". `http.server` preserves the case the client sent.
    """
    lowered = {str(key).lower(): value for key, value in (headers or {}).items()}
    value = lowered.get(name.lower(), "")
    return value if isinstance(value, str) else ""


def verify_delivery(
    provider_name: str,
    headers: dict,
    body: bytes,
    secret: str,
) -> dict:
    """Verify one delivery and return its parsed payload, or raise.

    `body` IS BYTES AND THERE IS NO STRING OVERLOAD. That is the raw-body trap
    made unrepresentable rather than documented: a caller holding a `str` has
    already decoded, and a caller holding a re-serialised dict cannot get here at
    all.

    THE ORDER, and it is `handler.py`'s with the Lambda-specific steps removed:

        1. unknown provider          -> 404  (before the secret is used)
        2. unusable secret           -> 500 via ValueError (NOT 401 -- "your
                                        signature failed" would be a lie)
        3. missing/malformed header  -> 401
        4. compare_digest fails      -> 401
        ── VERIFIED. ONLY NOW IS THE BODY PARSED. ──
        5. body is not JSON          -> 400
        6. body is not a JSON object -> 400

    Step 5 is AFTER verification deliberately. Parsing first would let an
    anonymous caller drive JSON parsing of a 4 MiB body, and it would return 400
    for an unsigned malformed request -- telling a caller their body was wrong
    when the truth is that they were never authenticated.

    THE SECRET IS A PARAMETER, NOT A LOOKUP. This module never reaches for
    Secrets Manager, an environment variable or a tenant's secret row: the caller
    supplies it. So the whole file is pure and testable with a literal, and there
    is no path from an unverified request to a secret read -- the property
    `handler.py` achieves by ordering, achieved here by not having the capability.
    """
    provider = provider_for(provider_name)

    if not secret or not secret.strip():
        # ValueError, not Unauthenticated. Same refusal `handler._webhook_secret`
        # makes: a whitespace-only key is a 1-3 byte key an attacker can guess,
        # and reporting it as a signature failure sends the next person to rotate
        # a secret that was always correct. The transport turns this into a 500.
        raise ValueError(
            f"the {provider.name} ingress secret is empty or whitespace only, so "
            f"every delivery would 401 while looking like a wrong signature. "
            f"Refused here instead."
        )

    provided = header_value(headers, provider.signature_header)
    if not provided:
        raise Unauthenticated()

    if provider.signs_body:
        if not provided.startswith(provider.prefix):
            # Covers a missing prefix and a malformed header, before any digest is
            # computed -- so a caller cannot make us do work with a header shape
            # we already know is wrong.
            raise Unauthenticated()
        expected = provider.prefix + hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
    else:
        # GITLAB. The header IS the secret, so the comparison is against the
        # secret itself. Still `compare_digest` -- a token compared with `==`
        # leaks its length and its first differing byte exactly as a digest does,
        # and a shared secret is the more valuable of the two because it does not
        # depend on a body.
        expected = secret

    if not hmac.compare_digest(
        provided.encode("utf-8", errors="replace"), expected.encode("utf-8")
    ):
        raise Unauthenticated()

    # ── verified. only now is the body parsed. ────────────────────────────────

    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        # `handler.py` says what this usually means: the App is set to
        # `application/x-www-form-urlencoded`. Naming that saves the next person a
        # round of guessing at a signature that verified correctly.
        raise BadRequest(
            "the delivery verified but its body is not JSON; set the webhook's "
            "content type to application/json"
        ) from exc

    if not isinstance(payload, dict):
        # A bare list or string is valid JSON and is not a webhook payload.
        # Refused rather than wrapped, because every reader downstream would then
        # have to handle a shape no provider sends.
        raise BadRequest(
            f"the delivery verified but its body is a JSON "
            f"{type(payload).__name__}, not an object"
        )

    return payload


def event_name(provider_name: str, headers: dict) -> str:
    """The provider's event name, verbatim.

    VERBATIM IS THE REQUIREMENT. `handler.py` sends GitHub's `x-github-event` as
    EventBridge's `DetailType` and explains the coupling: "inventing a value here
    means the rule matches nothing, the bus accepts the event, and nothing turns
    red." The same holds for any filter downstream of this function, so the value
    is passed through rather than normalised into a vocabulary of our own.
    """
    return header_value(headers, provider_for(provider_name).event_header)


def delivery_id(provider_name: str, headers: dict) -> str:
    """The provider's delivery id, or `""`.

    Used only for logging, and blank is legitimate -- a hand-rolled generic caller
    may send none. `handler.py` logs `"<no delivery id>"` for that case; this
    returns the blank and lets the caller decide, because a sentinel string
    stored as data reads as an id nobody can look up.
    """
    return header_value(headers, provider_for(provider_name).delivery_header)
