"""The control plane: submit a run, watch it, cancel it. OWNER: Lane K.

Spec `docs/final/01-specification.md` §12 (judge requirement 10), plan §4 LANE K.

THE CONTROL PLANE / DATA PLANE SPLIT, WHICH THIS PROJECT ALREADY UNDERSTANDS
===========================================================================
AgentCore draws the same line: `bedrock-agentcore-control` creates runtimes,
`bedrock-agentcore` invokes them, and CLAUDE.md records that describing "the
AgentCore client" is wrong because there are two. This package is the control
side of the pipeline -- it accepts work, reports on it and configures it. It
executes nothing. `scripts/worker.py` is the data plane and stays that way.

So every route here is a thin translation onto Phase 1's substrate:

    POST /v1/runs          -> queue.enqueue        (K1)
    GET  /v1/runs/<id>     -> queue.jobs_for_run   (K2)
    POST /v1/runs/<id>/cancel -> queue.complete    (K2)
    GET/PUT /v1/repositories/<name>/config          (K3)
    POST /v1/ingress/<provider>                     (K4)
    GET  /v1/openapi.json                           (K6)

**There is no second queue and no second state store.** A run's state document
stays in `runs/<id>.state.json` or DynamoDB where `gates.py` puts it, and a run's
progress is read off the queue's own rows. A control plane that kept its own copy
of either would be a second writer of a fact that already has one, which
`gates.py:37` names as how a single writer quietly becomes two.

=========================================================================
THIS API CANNOT APPROVE OR RESUME A GATE. THAT IS THE DESIGN, NOT A GAP.
=========================================================================
The three human gates are the last line in this system: CLAUDE.md's own summary
of the security thesis is that "a model that can be persuaded, distracted or
prompt-injected must not be the thing standing between a credential and `main`",
and the gates are what remains when the scanners miss something.

K5 asks for machine-to-machine auth for CI callers. A machine credential that
could approve a gate would defeat the gate for exactly the population it exists
to exclude: a CI job holding that token approves the security gate at machine
speed, with no human in the loop, and the run's `HumanDecision.by` would name a
service account. That is worse than no gate, because the record would still read
as a human decision.

So the refusal is structural rather than a policy note:

  * no route in `ROUTES` maps to `gates.resume` or `queue.resume`;
  * `api/service.py` does not import either name -- checked over the **AST** by
    `test_no_api_module_can_reach_a_gate_resume`, not by grep, because a comment
    naming `queue.resume` would satisfy a substring search while the call stayed
    (CLAUDE.md records that exact failure twice in one lane);
  * `CANCEL` is the one terminal transition the API can drive, and it can only
    end a run -- never advance one. A cancelled run is `rejected`; there is no
    argument to any function here that produces `approved`.

**What an unauthenticated caller can do: nothing.** Every route except
`GET /v1/health` and `POST /v1/ingress/<provider>` requires a bearer credential
that resolves to a tenant (`api/auth.py`), and the two exceptions are deliberate:
health reveals no tenant data, and the ingress path carries a provider HMAC
instead, verified before anything mutates -- the ordering `infra/ingress/handler.py`
established and this package reuses rather than reinvents.

**With no credentials configured, every authenticated route answers 401.** The
key store starts EMPTY and an empty store is a refusal, not an exemption: "nobody
has provisioned a key" and "this caller may do anything" are different facts and
must not share a representation. Same direction as `budgets.check` refusing a
tenant with no budget row, and for the same reason.

`agentorg/approve_server.py` remains what it was -- an unauthenticated,
loopback-only approval screen that MUST NOT be exposed off-host. This package
does not replace it, does not import it, and does not widen it. If this API ever
grows an approval route it needs a human identity on the credential, which is a
different scheme from the machine tokens here; that is stated in `auth.py` next
to the value that would have to change.

WHY THE STANDARD LIBRARY, MEASURED RATHER THAN ASSUMED
=====================================================
`starlette 1.6.0` and `uvicorn 0.52.4` are installed in `.venv-main` (fastapi is
NOT), so a framework was available and was still refused. Two reasons, both
mechanical:

  * `tests/test_agentcore_deploy_assets.py::test_requirements_covers_every_third_party_import_in_the_package`
    AST-walks every `agentorg/**/*.py` and fails when a third-party top-level
    import is absent from `agentorg/agents/requirements.txt`. So an import here
    is a dependency in all five arm64 agent images -- a web framework shipped to
    five containers that serve two routes each. `starlette` is already in that
    test's `_NOT_RUNTIME` exclusion list for `common/health.py`, described there
    as "dead code today"; importing it from a module that is NOT dead would make
    that exclusion a lie.
  * `agentorg/agents/server.py` and `agentorg/approve_server.py` are both
    `BaseHTTPRequestHandler`, so this is the third instance of a shape the
    repository already reviews confidently, not a fourth style.

THE HTTP CONTRACT IS `agents/server.py`'S, DELIBERATELY REUSED
==============================================================
Same codes for the same reasons, so a caller who has integrated against one
runtime does not learn a second vocabulary:

    400  bad Content-Length, empty body, body is not JSON
    401  no credential, or one that does not verify
    403  a credential that verifies but names another tenant's resource
    404  unknown route, or a run this tenant cannot see
    409  a conflicting state -- a cancel on a run that already ended
    413  over the cap, CHECKED BEFORE THE READ so a hostile length cannot make
         the process allocate
    422  a valid JSON body that is not a valid request model, with the detail
    500  an unhandled exception, with its type and message

**403 and 404 are not interchangeable here and the choice is per-resource.** A
run id is an unguessable uuid, so `403` on somebody else's run tells the caller
only what they already tried; a repository `full_name` is guessable (`acme/auth`),
so a cross-tenant read answers `404` -- distinguishing "not yours" from "no such
thing" would itself be the disclosure. `tests/test_tenancy_leak.py` records the
same distinction for the same reason, and this package inherits it rather than
inventing a second convention.

**Failures are not swallowed.** A 500 carries the exception type, exactly as
`agents/server.py` does: "a green response meaning 'the check did not run' is the
one answer this pipeline must never accept."
"""

from .auth import (
    KEY_PREFIX,
    Credential,
    InMemoryKeyStore,
    hash_secret,
    issue_key,
    key_store,
    parse_bearer,
    resolve,
    set_key_store,
)
from .errors import (
    ApiError,
    Conflict,
    Forbidden,
    NotFound,
    PayloadTooLarge,
    Unauthenticated,
    Unprocessable,
)
from .idempotency import IdempotencyStore, idempotency_store, set_idempotency_store
from .ingress import PROVIDERS, IngressProvider, verify_delivery
from .openapi import ROUTES, Route, openapi_document
from .service import (
    RepositoryConfig,
    RunStatus,
    RunSubmission,
    cancel_run,
    read_config,
    run_status,
    submit_run,
    write_config,
)

__all__ = [
    "KEY_PREFIX",
    "PROVIDERS",
    "ROUTES",
    "ApiError",
    "Conflict",
    "Credential",
    "Forbidden",
    "IdempotencyStore",
    "InMemoryKeyStore",
    "IngressProvider",
    "NotFound",
    "PayloadTooLarge",
    "RepositoryConfig",
    "Route",
    "RunStatus",
    "RunSubmission",
    "Unauthenticated",
    "Unprocessable",
    "cancel_run",
    "hash_secret",
    "idempotency_store",
    "issue_key",
    "key_store",
    "openapi_document",
    "parse_bearer",
    "read_config",
    "resolve",
    "run_status",
    "set_idempotency_store",
    "set_key_store",
    "submit_run",
    "verify_delivery",
    "write_config",
]
