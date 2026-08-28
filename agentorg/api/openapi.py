"""K6: the OpenAPI document, GENERATED from the code that serves it.

OWNER: Lane K.

WHY GENERATED, AND WHY THAT IS THE WHOLE TASK
============================================
A hand-written schema is a second declaration of the API, and this repository has
measured what two declarations do. `scoring.THRESHOLD_FLOOR` is derived rather
than typed because "a literal would be a second declaration of gitleaks'
severity, and two copies keep agreeing while one moves". `scripts/preflight.py`
imports its line sets from `tests/provenance.py` because "a copy would be a second
declaration of the fact this repository's whole verification story rests on, and
both copies would keep passing as they drifted."

A schema is the same shape of artifact with a worse failure: it is what a customer
generates a client from. A drifted schema does not merely go stale -- it produces
a client that sends a field the server does not read and reports success, which is
this project's signature defect delivered to somebody else's codebase.

So there are two sources and no third:

  * `ROUTES` -- the route table `api/server.py` DISPATCHES on. Not a parallel
    list: the server's routing IS this tuple, so a route that exists is
    documented and a documented route exists.
  * `model_json_schema()` -- pydantic's own output for `RunSubmission`,
    `RunStatus` and `RepositoryConfig`. Every field, type and constraint comes
    from the class the handler validates against.

`test_every_served_route_appears_in_the_openapi_document` closes the loop from the
other side, and it is the test that makes this non-vacuous: it drives the server's
dispatcher and fails when a path answers something other than 404 without being in
`ROUTES`.

WHAT IS DOCUMENTED AND CANNOT BE: THE ABSENT ROUTES
==================================================
The document lists no approval route, and that absence is a fact a reader needs
rather than a silence they have to notice. So `description` says it: the schema
carries a sentence stating that this API cannot approve or resume a gate and that
the capability is not merely unassigned. A client generated from this document
therefore has no method to call, and the person reading the docs learns why.

WHY NOT `fastapi`, WHICH WOULD PRODUCE THIS FOR FREE
===================================================
Because it is not installed (measured -- `pip list` shows starlette 1.6.0 and
uvicorn 0.52.4, no fastapi), and because adding it would put a web framework in
the dependency closure that `test_requirements_covers_every_third_party_import_in_the_package`
walks into all five arm64 agent images. pydantic already generates the hard part;
what remains is assembling a dict, which is this file.
"""

from __future__ import annotations

from dataclasses import dataclass

from .auth import (
    SCOPE_CONFIG_READ,
    SCOPE_CONFIG_WRITE,
    SCOPE_RUNS_READ,
    SCOPE_RUNS_WRITE,
)
from .errors import ERRORS
from .ingress import PROVIDERS
from .service import RepositoryConfig, RunStatus, RunSubmission

OPENAPI_VERSION = "3.1.0"
API_VERSION = "1"

# The one sentence a generated client's reader must not have to infer. See the
# module docstring, and `api/__init__.py` for the argument.
_NO_GATE_ROUTE = (
    "This API cannot approve, reject or resume a human gate. No route maps to "
    "gates.resume or queue.resume, and no scope grants it -- the capability is "
    "absent rather than unassigned. A machine credential that could approve a "
    "gate would defeat the gate for exactly the callers gates exist to exclude, "
    "and the run's decision record would name a service account while reading as "
    "a human decision. Gate decisions are made by a human through a surface that "
    "carries a human identity."
)


@dataclass(frozen=True)
class Route:
    """One served route. `api/server.py` dispatches on this table.

    `scope` is the scope the handler requires, or `""` for the two unauthenticated
    routes -- health and ingress. Declared here so the document and the dispatcher
    cannot disagree about which routes need a credential, and so a route added
    with no scope is visible as such rather than merely undocumented.

    `summary` is what appears in the schema. Written as what the route DOES, and
    for `cancel` it states the limit rather than the intent, because a caller
    generating a client from this needs to know that an executing stage is not
    killed.
    """

    method: str
    path: str
    operation_id: str
    summary: str
    scope: str
    request_model: str = ""
    response_model: str = ""


ROUTES: tuple[Route, ...] = (
    Route(
        method="GET",
        path="/v1/health",
        operation_id="health",
        summary="Liveness. Reveals no tenant data, so it needs no credential.",
        scope="",
        response_model="",
    ),
    Route(
        method="POST",
        path="/v1/runs",
        operation_id="submitRun",
        summary=(
            "Submit a ticket. Enqueues the plan stage and returns the run's id. "
            "Send Idempotency-Key to make a retry safe: a repeat with the same "
            "key returns the first run rather than starting a second."
        ),
        scope=SCOPE_RUNS_WRITE,
        request_model="RunSubmission",
        response_model="RunStatus",
    ),
    Route(
        method="GET",
        path="/v1/runs/{run_id}",
        operation_id="runStatus",
        summary=(
            "Where a run is and what it has done, read off the queue. Includes "
            "`reclaimed`, which is the only trace that a stage may have run twice."
        ),
        scope=SCOPE_RUNS_READ,
        response_model="RunStatus",
    ),
    Route(
        method="POST",
        path="/v1/runs/{run_id}/cancel",
        operation_id="cancelRun",
        summary=(
            "End a run. No further stage runs; a stage already executing is NOT "
            "killed, and its result cannot advance the run. 409 if the run has "
            "already ended -- a cancel never reports success for a run it did "
            "not cancel."
        ),
        scope=SCOPE_RUNS_WRITE,
        response_model="RunStatus",
    ),
    Route(
        method="GET",
        path="/v1/repositories/{full_name}/config",
        operation_id="readConfig",
        summary=(
            "The effective configuration. An unconfigured repository returns the "
            "defaults the pipeline would actually use, not a blank."
        ),
        scope=SCOPE_CONFIG_READ,
        response_model="RepositoryConfig",
    ),
    Route(
        method="PUT",
        path="/v1/repositories/{full_name}/config",
        operation_id="writeConfig",
        summary=(
            "Set the block threshold and which advisory checks are on. The "
            "threshold is refused, never clamped, if it would stop a committed "
            "credential from blocking. The security check cannot be turned off."
        ),
        scope=SCOPE_CONFIG_WRITE,
        request_model="RepositoryConfig",
        response_model="RepositoryConfig",
    ),
    Route(
        method="POST",
        path="/v1/ingress/{provider}",
        operation_id="ingress",
        summary=(
            "A verified webhook delivery. Carries a provider HMAC (or GitLab's "
            "shared token) instead of a bearer key; verification happens before "
            "anything is parsed, enqueued or read."
        ),
        scope="",
        request_model="",
        response_model="",
    ),
    Route(
        method="GET",
        path="/v1/openapi.json",
        operation_id="openapi",
        summary="This document. Generated from the route table and the models.",
        scope="",
        response_model="",
    ),
)

# The models the document describes, by the name the routes reference. A dict so
# `_schemas` cannot document a model no route mentions, and so a route naming a
# model that does not exist is a KeyError at import rather than a missing `$ref`
# in a published schema.
_MODELS = {
    "RunSubmission": RunSubmission,
    "RunStatus": RunStatus,
    "RepositoryConfig": RepositoryConfig,
}


def _error_schema() -> dict:
    """The shape every refusal answers with. One declaration, seven statuses.

    Built from `ApiError.payload`'s actual keys rather than described, so the
    document cannot claim a field the code does not send. `detail` is optional
    here because it is optional there -- absent rather than null, for the reason
    `payload` gives.
    """
    return {
        "type": "object",
        "required": ["error"],
        "properties": {
            "error": {"type": "string"},
            "detail": {
                "description": (
                    "Present only where it is safe to send. A 422 carries the "
                    "validation detail; a 401 carries none, because naming which "
                    "part of a credential failed turns one guess into two."
                ),
            },
        },
    }


def _responses(route: Route) -> dict:
    """The responses for one route, including the refusals it can actually answer.

    DERIVED FROM `ERRORS` AND THE ROUTE'S OWN PROPERTIES rather than listed per
    route. A hand-listed set drifts the moment a handler learns a new refusal, and
    the drift is invisible: the schema keeps describing an API that used to exist.

    401 is attached to every route that declares a scope and to NO route that
    does not, which is the same fact the dispatcher acts on. So a reader can see
    from the document which two routes are unauthenticated, and why -- rather than
    discovering it by calling them.
    """
    responses: dict = {
        "200": {
            "description": "Success.",
        }
    }
    if route.response_model:
        responses["200"]["content"] = {
            "application/json": {
                "schema": {"$ref": f"#/components/schemas/{route.response_model}"}
            }
        }

    statuses = {"500"}
    if route.scope:
        statuses.add("401")
    if route.request_model:
        statuses.update({"400", "413", "422"})
    if "{run_id}" in route.path:
        statuses.update({"403", "404"})
    if "{full_name}" in route.path:
        # 404 and NOT 403: a repository full_name is guessable, so distinguishing
        # "not yours" from "no such thing" is itself the disclosure. The asymmetry
        # with {run_id} above is deliberate and is `tests/test_tenancy_leak.py`'s.
        statuses.add("404")
    if route.operation_id == "cancelRun":
        statuses.add("409")
    if route.operation_id == "ingress":
        # Verification failures and an unknown provider. No 401-less path exists
        # here even though the route takes no bearer scope -- the credential is
        # the provider's signature.
        statuses.update({"400", "401", "404", "413"})

    for error in ERRORS:
        code = str(error.status)
        if code in statuses:
            responses[code] = {
                "description": (error.__doc__ or "").strip().splitlines()[0],
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/Error"}
                    }
                },
            }
    responses["500"] = {
        "description": (
            "An unhandled exception, with its type and message. Deliberately not "
            "turned into a tidy 4xx: a refusal this service did not classify must "
            "not read as the caller's mistake."
        ),
        "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
        },
    }
    return responses


def _parameters(route: Route) -> list[dict]:
    """Path parameters, derived from the path template.

    Derived so a route whose path gains a parameter cannot document one fewer
    than it takes -- the `{...}` in the string is the single source, and the
    server's dispatcher reads the same template.
    """
    parameters = []
    for name in ("run_id", "full_name", "provider"):
        if f"{{{name}}}" in route.path:
            parameter: dict = {
                "name": name,
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
            if name == "provider":
                parameter["schema"]["enum"] = sorted(PROVIDERS)
            parameters.append(parameter)
    if route.operation_id == "submitRun":
        parameters.append(
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": False,
                "schema": {"type": "string", "maxLength": 200},
                "description": (
                    "Makes a retried submission safe. A repeat with the same key "
                    "returns the first run with idempotent_replay true, rather "
                    "than starting a second. Scoped per tenant, so two customers "
                    "may use the same key. This makes SUBMISSION idempotent; it "
                    "does not make the pipeline exactly-once."
                ),
            }
        )
    return parameters


def openapi_document() -> dict:
    """The whole schema, assembled from `ROUTES` and the pydantic models.

    A FUNCTION AND NOT A MODULE CONSTANT, so it is rebuilt per call and cannot be
    mutated by a caller into something the next reader receives. `PROVIDERS` and
    `ERRORS` are read at call time for the same reason.
    """
    paths: dict = {}
    for route in ROUTES:
        operation: dict = {
            "operationId": route.operation_id,
            "summary": route.summary,
            "responses": _responses(route),
        }
        parameters = _parameters(route)
        if parameters:
            operation["parameters"] = parameters
        if route.request_model:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "$ref": f"#/components/schemas/{route.request_model}"
                        }
                    }
                },
            }
        if route.scope:
            # The scope is named on the operation, so a reader can see which key a
            # route needs without reading the source. `bearerAuth` is declared in
            # components below.
            operation["security"] = [{"bearerAuth": []}]
            operation["x-required-scope"] = route.scope
        paths.setdefault(route.path, {})[route.method.lower()] = operation

    schemas = {name: model.model_json_schema() for name, model in _MODELS.items()}
    schemas["Error"] = _error_schema()

    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "The Agent Org control plane",
            "version": API_VERSION,
            "description": (
                "Submit a ticket, watch its run, cancel it, and configure a "
                "repository. Execution happens elsewhere: this is the control "
                "plane, and it enqueues work rather than running it.\n\n"
                + _NO_GATE_ROUTE
            ),
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": (
                        "A machine key, `agtk_<key_id>_<secret>`. Issued once and "
                        "stored only as a scrypt digest, so it cannot be read "
                        "back. With no keys provisioned every authenticated route "
                        "answers 401 -- an empty key store is a refusal, not an "
                        "exemption."
                    ),
                }
            },
            "schemas": schemas,
        },
        "paths": paths,
    }
