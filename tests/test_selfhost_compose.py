"""The self-hosted Compose stack, as DATA. Lane F, task F4.

WHY THIS FILE EXISTS AT ALL. `docker compose up` cannot run in this suite -- there
is no daemon, and a test that needed one would be skipped on every machine that
matters, which is worse than absent because it reads as coverage. So these tests
assert the stack's SECURITY AND CORRECTNESS PROPERTIES over the parsed YAML, which
is the same thing `tests/test_ingress_terraform.py` does for the ingress module.

WHAT THAT CAN AND CANNOT CATCH, stated so nobody quotes a pass as more than it is.
It catches a port published to every interface, a fixture-serving model
configuration, a missing dependency ordering, and an AWS credential appearing in
the environment of a stack whose entire claim is that it makes no AWS call. It
cannot catch an image that fails to build or a service that starts and crashes.
`test_the_file_states_that_up_was_never_run` pins the honesty of the header rather
than the behaviour of the stack, because the header is the only thing here that
can tell a reader which of those two they are looking at.
"""

from __future__ import annotations

import pathlib

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML ships with strands-agents")

COMPOSE_PATH = (pathlib.Path(__file__).resolve().parent.parent
                / "infra" / "selfhost" / "docker-compose.yml")


@pytest.fixture(scope="module")
def compose() -> dict:
    """The parsed stack.

    Asserts non-empty before returning: a `yaml.safe_load` of a missing or empty
    file is `None`, and every test below would then pass vacuously against a stack
    that does not exist.
    """
    assert COMPOSE_PATH.exists(), f"{COMPOSE_PATH} is missing"
    parsed = yaml.safe_load(COMPOSE_PATH.read_text())
    assert parsed and parsed.get("services"), (
        "the compose file parsed to nothing; every test in this file would pin "
        "nothing"
    )
    return parsed


@pytest.fixture(scope="module")
def source() -> str:
    return COMPOSE_PATH.read_text()


def test_every_published_port_is_bound_to_loopback(compose: dict):
    """`- "5432:5432"` binds 0.0.0.0, and Docker bypasses most host firewalls.

    THE ASSERTION IS OVER EVERY SERVICE, not over the two that exist today, so a
    service added later cannot publish to the world without failing this. The
    database holds the audit trail and the model answers prompts; neither belongs
    on a laptop's public interfaces at a committed dev password.
    """
    published = 0
    for name, service in compose["services"].items():
        for mapping in service.get("ports", []) or []:
            published += 1
            assert isinstance(mapping, str), (
                f"{name} publishes a port in long form; this test reads the "
                f"short string form and would not inspect it"
            )
            assert mapping.startswith("127.0.0.1:"), (
                f"{name} publishes {mapping!r}, which binds every interface. "
                f"Prefix it with 127.0.0.1:"
            )
    assert published >= 2, (
        "fewer than two published ports were found; this test may be reading a "
        "file whose shape has changed"
    )


def test_no_aws_credential_or_region_reaches_any_service(compose: dict):
    """The stack's whole claim is that it makes no AWS call.

    A leaked `AWS_ACCESS_KEY_ID` here would not merely be untidy: it would make
    the Bedrock path AVAILABLE, so a mistake in `LLM_BASE_URL` would fail over to
    AWS silently instead of falling back to a fixture. `AWS_REGION` alone is
    enough for boto3 to try.
    """
    forbidden = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                 "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE", "AWS_ROLE_ARN")
    for name, service in compose["services"].items():
        environment = service.get("environment", {}) or {}
        keys = environment if isinstance(environment, dict) else {
            entry.split("=", 1)[0] for entry in environment
        }
        for banned in forbidden:
            assert banned not in keys, (
                f"{name} sets {banned}; this stack must not be able to reach AWS "
                f"at all, and a set region is enough for boto3 to try"
            )


def test_the_model_key_is_not_the_refusing_default(compose: dict):
    """`not-needed` sends every agent to its fixture while the run stays green.

    MEASURED: with `LLM_BASE_URL` set and `LLM_API_KEY` left at its default,
    `llm.available()` returns False, because both it and `create_model()` refuse
    that literal. A local gateway ignores the value, so nothing downstream would
    complain -- the only symptom is `_source=fixture` in output nobody reads.
    """
    worker = compose["services"]["worker"]["environment"]
    assert worker["LLM_BASE_URL"], "the worker has no gateway URL, so it would use Bedrock"
    assert worker["LLM_API_KEY"] != "not-needed", (
        "LLM_API_KEY is the literal that available() and create_model() both "
        "refuse; every agent would serve its fixture with the stack green"
    )
    assert worker["LLM_API_KEY"], "an empty LLM_API_KEY is refused the same way"


def test_the_queue_and_the_application_share_one_database(compose: dict):
    """Two databases would let a run be enqueued and not recorded.

    The queue's correctness argument is that a pause is a durable ROW, which
    requires the row and the run to be in one place. Asserted by matching the
    DSN's host against the postgres SERVICE NAME rather than against the literal
    `postgres`, so renaming the service cannot silently split the two.
    """
    assert "postgres" in compose["services"], "the database service is missing"
    worker = compose["services"]["worker"]["environment"]
    assert worker["QUEUE_BACKEND"] == "postgres", (
        "the worker is not on the durable queue backend, so a pause would not "
        "survive a restart"
    )
    assert "@postgres:" in worker["QUEUE_DSN"], (
        "the queue DSN does not point at the postgres service in this stack"
    )


def test_the_model_is_pulled_before_the_worker_starts(compose: dict):
    """An absent pull makes the first agent call fail INTO A FIXTURE.

    `llm.text()` catches every exception by design, so a model that is not there
    yet produces a fixture run with the stack reporting healthy. The ordering is
    therefore a correctness property, not a convenience, and
    `service_completed_successfully` is the only condition that means the pull
    FINISHED rather than merely started.
    """
    worker = compose["services"]["worker"]
    depends = worker.get("depends_on", {})
    assert "model-pull" in depends, (
        "the worker does not wait for the model pull; its first agent call would "
        "fail and serve a fixture while the stack looked healthy"
    )
    assert depends["model-pull"]["condition"] == "service_completed_successfully", (
        "the worker waits on the pull STARTING rather than FINISHING"
    )
    assert depends["postgres"]["condition"] == "service_healthy", (
        "the worker does not wait for postgres to answer queries; pg_isready "
        "succeeds seconds before the first statement will"
    )
    assert compose["services"]["model-pull"].get("restart") == "no", (
        "the pull service would restart after succeeding, re-pulling 4.7 GB"
    )


def test_scanners_are_REQUIRED_on_the_stack_that_carries_them(compose: dict):
    """A missing binary must be a FAULT here, not a dev affordance.

    This image carries gitleaks, trivy and semgrep, so it is the one stack that
    can honestly demand them. With `SCANNERS_REQUIRED` false, a scanner that
    failed to install becomes a `fixture-fallback` and the poisoned ticket blocks
    for the WRONG REASON -- with `provenance` the only field that would say so.
    """
    worker = compose["services"]["worker"]["environment"]
    assert worker["SCANNERS_REQUIRED"] == "true", (
        "a missing scanner would degrade to a fixture and the poisoned ticket "
        "would block for the wrong reason"
    )


def test_the_file_states_that_up_was_never_run(source: str):
    """The honesty of the header, pinned.

    A future edit that quietly deletes the caveat would leave a file readable as
    a demonstrated stack. This asserts the disclaimer's SUBSTANCE -- that `up`
    was not run and the builds are unproven -- rather than any one sentence, so
    rewording is allowed and removing the admission is not.
    """
    assert "NOT VERIFIED" in source
    assert "docker compose up` has never been executed" in source, (
        "the file no longer admits that the stack was never started; a reader "
        "would take a parsing compose file for a running one"
    )
    assert "unproven" in source


def test_the_approval_server_is_not_a_service(compose: dict):
    """It has no authentication and resumes a run past the SECURITY gate.

    Kept as a test rather than a comment because the temptation is real: the
    stack has no UI, `approve_server` is the only web surface in the repository,
    and adding it would mean binding 0.0.0.0 inside the network namespace.
    """
    for name, service in compose["services"].items():
        command = " ".join(str(part) for part in (service.get("command") or []))
        assert "approve_server" not in command, (
            f"{name} runs approve_server, which has no authentication and can "
            f"resume a paused run past the security gate"
        )
