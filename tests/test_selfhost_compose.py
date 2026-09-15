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


def _environment(service: dict) -> dict:
    """A service's environment as a dict, whatever form compose declared it in.

    Compose accepts both a mapping and a `NAME=value` list. Every test below reads
    keys, so a list-form service would silently be inspected as a set of strings
    and every `in` check against it would answer about the wrong thing.
    """
    environment = service.get("environment", {}) or {}
    if isinstance(environment, dict):
        return environment
    return dict(
        entry.split("=", 1) if "=" in entry else (entry, "")
        for entry in environment
    )


def test_no_STATIC_aws_key_is_written_into_this_file(compose: dict):
    """Credentials come from the operator's mounted session, never from here.

    THE CLAIM NARROWED ON 2026-09-15 AND THIS TEST NARROWED WITH IT. Tenancy moved
    to DynamoDB, so the stack now reaches AWS deliberately and the previous
    assertion -- that no service may carry `AWS_REGION` at all -- became a
    statement about a design that no longer exists. What did NOT change is the
    rule it was really protecting: CLAUDE.md's `zero static AWS keys anywhere`, and
    a committed compose file is exactly where one would land.

    So a region and a credentials-FILE path are now expected; a literal key, secret
    or session token is still forbidden. `AWS_PROFILE` is forbidden too: it names a
    profile in the operator's own file, which makes what this stack can reach
    depend on a machine nobody reviews.
    """
    forbidden = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                 "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_ROLE_ARN")
    for name, service in compose["services"].items():
        keys = _environment(service)
        for banned in forbidden:
            assert banned not in keys, (
                f"{name} sets {banned}. Credentials must arrive through the "
                f"read-only ~/.aws mount, not through this file -- it is committed"
            )


def test_every_service_that_can_reach_aws_pins_its_model_path(compose: dict):
    """MEASURED DEFECT, 2026-09-15: `api` could make a billable Bedrock call.

    `llm.available()` reads `LLM_DISABLED`, then `LLM_BASE_URL`, and ONLY when both
    are empty does it fall through to boto3 and answer True if a credential exists.
    Before the DynamoDB migration no service here carried a credential, so that
    final branch was unreachable and nobody had to think about it -- the test
    forbidding credentials was doing the work.

    Adding the ~/.aws mount removed that guard along with the thing it guarded, and
    `api` had neither variable set. Measured on the parsed file:

        service  LLM_DISABLED  LLM_BASE_URL           -> reaches
        worker   (unset)       http://model:11434/v1     gateway
        api      (unset)       (unset)                   YES - BEDROCK
        web      true          (unset)                   no

    A stack whose entire claim is that it is self-hosted must not be one typo from
    a live model call, so every credentialed service states which it is. This is
    the same shape as `SCANNERS_REQUIRED` below: the honest failure is loud.
    """
    credentialed = []
    for name, service in compose["services"].items():
        environment = _environment(service)
        if any(key.startswith("AWS_") for key in environment):
            credentialed.append(name)
            disabled = environment.get("LLM_DISABLED") == "true"
            gateway = bool(environment.get("LLM_BASE_URL"))
            assert disabled or gateway, (
                f"{name} carries AWS credentials and sets neither LLM_DISABLED nor "
                f"LLM_BASE_URL, so llm.available() falls through to boto3, finds the "
                f"mounted session, and this 'self-hosted' stack calls Bedrock"
            )
    assert credentialed, (
        "no service carries an AWS_* variable; this test would pin nothing. If the "
        "stack no longer reaches AWS, restore the stronger no-credential assertion"
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


def test_the_queue_and_the_application_share_one_table(compose: dict):
    """Two stores would let a run be enqueued and not recorded.

    UNCHANGED ARGUMENT, NEW STORE. The queue's correctness rests on a pause being a
    durable ROW, which requires the row and the run to live in one place; only the
    place changed on 2026-09-15, from a Postgres DSN to a DynamoDB table.

    Asserted across EVERY service that names a table rather than against the
    worker alone, because the split this forbids is between two services, and a
    test reading one of them cannot see it.
    """
    tables = {
        name: _environment(service)["TENANCY_TABLE"]
        for name, service in compose["services"].items()
        if "TENANCY_TABLE" in _environment(service)
    }
    assert len(tables) >= 2, (
        f"only {sorted(tables)} name a table; this test would pin nothing about "
        f"two services agreeing"
    )
    assert len(set(tables.values())) == 1, (
        f"the services disagree about which table holds the run index: {tables}. A "
        f"run could be enqueued in one and recorded in the other"
    )

    for name, service in compose["services"].items():
        environment = _environment(service)
        if "QUEUE_BACKEND" not in environment:
            continue
        assert environment["QUEUE_BACKEND"] == "dynamodb", (
            f"{name} is not on the durable queue backend, so a pause would not "
            f"survive a restart"
        )
        assert "QUEUE_DSN" not in environment, (
            f"{name} still carries a QUEUE_DSN. Postgres was retired; a leftover "
            f"DSN is a second store nobody reads and a password nobody rotates"
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
    # THE POSTGRES CONDITION THAT STOOD HERE IS GONE WITH THE SERVICE, 2026-09-15.
    # It asserted `service_healthy` because `pg_isready` succeeds seconds before the
    # first statement will. DynamoDB is a managed endpoint with nothing in this
    # stack to wait for, so there is no ordering left to get wrong -- and an
    # assertion kept against a service that no longer exists is a KeyError dressed
    # as coverage.
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
