"""The refusal vocabulary. One exception per HTTP status, and no `Exception`.

OWNER: Lane K.

WHY A CLASS PER STATUS RATHER THAN ONE ERROR CARRYING A NUMBER
=============================================================
Because a caller inside this package raises the refusal at the point it discovers
it, and the transport turns it into a response somewhere else entirely. With one
`ApiError(status=403)` the number is an argument, and an argument can be wrong at
the call site with nothing to catch it -- `raise ApiError(404, ...)` where 403 was
meant reads as correct code. With a class per status the choice is a name, and a
test can assert `pytest.raises(Forbidden)` rather than comparing an integer it
also had to write down.

THE SAME DISTINCTION `agent_client.py`'S CLASSIFIER MAKES, ONE LAYER UP
======================================================================
That module has six failure classes plus an explicit UNCLASSIFIED branch, and its
reason is quoted here because it applies unchanged: "a classifier that guesses is
worse than one admitting it did not recognise the error, because the guess is what
makes a caller wait out a condition that will never clear."

So there is deliberately NO catch-all `ApiError` raised anywhere. An exception
this package did not name reaches the transport as a 500 with its type -- which is
`agents/server.py`'s rule and is the honest answer for something nobody
classified. Turning an unrecognised failure into a tidy 400 would tell a caller
their request was wrong when the truth is that this service broke.

`detail` IS OPTIONAL AND IS NOT ALWAYS SAFE TO SEND
===================================================
`Unprocessable` carries pydantic's own `errors()`, which names fields and is
exactly what makes a caller's mistake fixable without server logs -- the reason
`agents/server.py` returns it on a 422.

`Unauthenticated` deliberately carries NO detail beyond a fixed sentence. The
ingress Lambda's `_response` states the rule this follows: a public endpoint's
body "never contains the secret, the expected signature, or any part of either --
handing back the digest we wanted would let a caller obtain a valid signature for
a body of their choosing, which is the whole authentication scheme." An auth
error that distinguished "no such key id" from "wrong secret" is the same
disclosure in a smaller form: it turns one guess into two.
"""

from __future__ import annotations


class ApiError(Exception):
    """Base for every refusal this package raises deliberately.

    `status` is a CLASS attribute, not a constructor argument, so a subclass
    cannot be raised with the wrong number. There is no `ApiError(404, ...)`
    form, and that is the point -- see the module docstring.

    Never raised directly: it has no status of its own, and a subclass that
    forgot to declare one would inherit a plausible-looking default. `status`
    below is `0`, which is not a valid HTTP status, so a subclass that forgets
    fails loudly at the transport rather than answering something wrong.
    """

    status = 0

    def __init__(self, message: str, *, detail: object = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def payload(self) -> dict:
        """The response body for this refusal.

        `detail` is omitted entirely when absent rather than sent as `null`: a
        key whose value is null reads as a field the server tried to fill and
        could not, which is a different fact from one it never had.
        """
        body: dict = {"error": self.message}
        if self.detail is not None:
            body["detail"] = self.detail
        return body


class BadRequest(ApiError):
    """400. The request could not be read as JSON at all."""

    status = 400


class Unauthenticated(ApiError):
    """401. No credential, or one that did not verify.

    ONE MESSAGE FOR EVERY CAUSE, and the causes are genuinely different: a
    missing header, a malformed header, an unknown key id, a revoked key, and a
    wrong secret all arrive here. Naming which would tell an attacker whether a
    key id exists, which turns one guess into two -- the same reason
    `accessors._require` raises `NotFound` rather than `CrossTenantAccess` for a
    guessable key.

    Deliberately NOT 403. This project already carries the "denied versus not
    ready yet" distinction (`agent_client`'s classifier, and the ingress
    handler's 500-not-401 for an unreadable secret); the matching one here is
    "we do not know who you are" versus "we know, and no".
    """

    status = 401

    def __init__(self, message: str = "unauthorized", *, detail: object = None) -> None:
        # `detail` is accepted for signature symmetry and then DROPPED, so a
        # future caller cannot leak a reason through it by accident. Pinned by
        # test_an_unauthenticated_refusal_carries_no_detail_even_when_given_one.
        super().__init__(message, detail=None)


class Forbidden(ApiError):
    """403. A verified credential asking for something outside its tenant.

    Used only where the resource identifier is UNGUESSABLE -- a run id is a
    uuid, so telling the caller "not yours" reveals nothing they did not
    already supply. For a guessable identifier (a repository `full_name`) the
    answer is `NotFound` instead, because distinguishing the two cases is the
    disclosure. `tests/test_tenancy_leak.py`'s docstring records the same
    split for the same reason.
    """

    status = 403


class NotFound(ApiError):
    """404. No such route, or no such resource in this tenant's scope."""

    status = 404


class Conflict(ApiError):
    """409. The resource is in a state this request cannot be applied to.

    The cancel path's refusal: a run that already ended cannot be cancelled.
    Deliberately not 400 -- the request was well formed and would have been
    valid a minute earlier, and a caller retrying a cancel after a run blocked
    needs to be able to tell "you asked wrongly" from "you asked too late".

    That distinction is why cancel does not answer 200 for an already-terminal
    run either. A cancel that reports success for a run it did not cancel is
    this repository's signature defect, and `queue.complete` refuses the
    overwrite underneath for the same reason (measured on run 32509257195,
    where a recorder erased a poisoned run's `status=blocked`).
    """

    status = 409


class PayloadTooLarge(ApiError):
    """413. Over the cap, refused BEFORE the body is read.

    `agents/server.py` checks the length ahead of `rfile.read` "so a malformed
    or hostile Content-Length cannot make the container allocate without
    bound", and this package's transport does the same. The status is declared
    here so the ordering can be asserted against a named class rather than a
    number that appears in three files.
    """

    status = 413


class Unprocessable(ApiError):
    """422. Valid JSON that is not a valid request model.

    Carries pydantic's `errors()` as `detail`, matching `agents/server.py`'s
    422 for a payload that is not a `RunState`: "returning the validation
    detail is what makes a caller's mistake fixable without container logs."
    """

    status = 422


# Every status this package can answer with, derived from the classes rather than
# listed. The OpenAPI document reads this, so a new refusal class cannot be
# omitted from the published schema -- and `test_every_api_error_declares_a_real_
# status` walks it to refuse a subclass that left `status` at 0.
ERRORS: tuple[type[ApiError], ...] = (
    BadRequest,
    Unauthenticated,
    Forbidden,
    NotFound,
    Conflict,
    PayloadTooLarge,
    Unprocessable,
)
