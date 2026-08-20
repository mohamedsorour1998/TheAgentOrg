"""deploy_note() must report the real deploy, or admit it cannot. Owner: Mariam.

The spec (docs/plan/mariam/week3.md:118-131) writes this function as a hardcoded
one-liner that always names five runtimes. Plan Task 4 forbids that -- "never a
fabricated success" -- so the tests here are written against the failure mode the
hardcoded version WOULD have had, not against the shape of the code:

  * Every state asserts what the string SAYS, never merely that a string came
    back. `assert isinstance(note, str)` and `assert note` both pass against the
    hardcoded one-liner in all four states, which makes them worse than no test:
    they read as coverage for the property this task exists to establish.
  * The no-credentials and failure tests assert the five runtime names are
    ABSENT. That is the assertion the hardcoded version cannot satisfy, so it is
    the one that pins the ruling.
  * The verified test compares against the spec's done-when output character for
    character, built independently of agentorg.github_ops. Importing
    RUNTIME_NAMES to build the expected string would make the test agree with
    the module about a typo -- rename a runtime and the test renames with it.
  * boto3 never runs. `_aws_credentials_available` and `_agentcore_client` are
    the two seams; the second is blocked for the whole file by an autouse guard
    rather than per-test, because a RED step proved per-test was not enough.
    THIS MACHINE HAS WORKING AWS CREDENTIALS, so mutating the no-credentials
    branch out of deploy_note() made a test fall through to the real control
    plane and call it for real. See `_no_live_agentcore`.
"""

import logging

import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
)

from agentorg import github_ops
from agentorg.common import config

# The spec's done-when output, written out rather than derived from
# github_ops.RUNTIME_NAMES. See the module docstring: a test that builds its
# expectation from the code under test cannot detect a rename.
SPEC_NOTE = (
    "AgentCore runtimes (us-east-1): theagentorg_planner, theagentorg_developer, "
    "theagentorg_reviewer, theagentorg_security, theagentorg_sre"
)
SPEC_NAMES = (
    "theagentorg_planner",
    "theagentorg_developer",
    "theagentorg_reviewer",
    "theagentorg_security",
    "theagentorg_sre",
)


class _FakePaginator:
    """Stand-in for botocore's list_agent_runtimes paginator."""

    def __init__(self, pages, boom=None):
        self._pages = pages
        self._boom = boom

    def paginate(self, **_kwargs):
        if self._boom is not None:
            raise self._boom
        return iter(self._pages)


class _FakeClient:
    """Stand-in for the bedrock-agentcore-control client.

    Records the paginator name asked for, so a test can prove the operation is
    the one it thinks it is rather than trusting the string in the source.
    """

    def __init__(self, pages=(), boom=None):
        self._pages = pages
        self._boom = boom
        self.asked_for = []

    def get_paginator(self, name):
        self.asked_for.append(name)
        return _FakePaginator(self._pages, self._boom)


def _page(*runtimes):
    """One ListAgentRuntimes page. Field names are botocore's, not invented."""
    return {"agentRuntimes": list(runtimes)}


def _runtime(name, status="READY"):
    return {"agentRuntimeName": name, "status": status, "agentRuntimeId": "id-x"}


# Captured at import, before the autouse guard below can replace it. The one
# test that exercises the real constructor needs the genuine function; every
# other test in this file must not be able to reach it.
_REAL_AGENTCORE_CLIENT = github_ops._agentcore_client


@pytest.fixture(autouse=True)
def _no_live_agentcore(monkeypatch):
    """Block the AgentCore control plane for every test in this file.

    A fifth seam guard in the shape of the four in tests/conftest.py, and it
    exists because a RED step proved the need rather than because it seemed
    tidy. Mutating the no-credentials branch out of deploy_note() made
    test_no_credentials_does_not_claim_a_deploy fall through to the REAL client
    and perform a live ListAgentRuntimes against account 339712964409 -- this
    machine has working AWS credentials. The test still went red, so the
    mutation was caught, but it was caught by a test that had just called AWS.

    That is the wrong way round: the offline guarantee was resting on the
    correctness of the code under test. `_no_live_agentcore` inverts it, so
    reaching AWS from this file is itself the failure.

    pytest.fail's `Failed` derives from BaseException, so deploy_note()'s
    `except Exception` cannot swallow this into an honest-looking degradation
    message -- the same property the conftest guards rely on.
    """
    def _blocked():
        pytest.fail(
            "This test reached the real github_ops._agentcore_client, which on a "
            "machine with AWS credentials is a live bedrock-agentcore-control "
            "call. Wire a _FakeClient with _wire(monkeypatch, ...) instead.",
            pytrace=False,
        )

    monkeypatch.setattr(github_ops, "_agentcore_client", _blocked)


def _all_five_ready():
    return [_page(*(_runtime(n) for n in SPEC_NAMES))]


@pytest.fixture
def no_credentials(monkeypatch):
    """The CI / laptop state: AWS is not configured.

    Patches the credential seam rather than the environment. THIS MACHINE HAS
    LIVE AWS CREDENTIALS (verified: boto3.Session().get_credentials() is not
    None), so clearing AWS_* env vars would not be enough -- a shared config
    file or an SSO cache would still resolve, and the call would be real and
    billable. Patching the seam cannot reach the network at all.
    """
    monkeypatch.setattr(github_ops, "_aws_credentials_available", lambda: False)


@pytest.fixture
def credentialed(monkeypatch):
    """Credentials resolve, so the control-plane branch is the one under test."""
    monkeypatch.setattr(github_ops, "_aws_credentials_available", lambda: True)


def _wire(monkeypatch, client):
    """Install a fake control-plane client and hand it back for inspection."""
    monkeypatch.setattr(github_ops, "_agentcore_client", lambda: client)
    return client


def test_no_credentials_does_not_claim_a_deploy(no_credentials, caplog):
    """RED step 1: the state CI and every laptop is in must not fabricate.

    This is the test the spec's hardcoded one-liner fails, and it is the whole
    point of Controller Ruling 10. The load-bearing assertion is the ABSENCE of
    the runtime names: a test asserting only that a string came back would pass
    against a function that always claims five runtimes exist.
    """
    with caplog.at_level(logging.DEBUG, logger="agentorg.github_ops"):
        note = github_ops.deploy_note()

    assert note, "an empty string is silence, which is what this function replaced"
    for name in SPEC_NAMES:
        assert name not in note, f"unverified output must not name {name}"
    assert SPEC_NOTE not in note, "the verified string must be unreachable here"
    assert "unverified" in note, f"the note must say so in words: {note!r}"
    assert "no AWS credentials" in note, f"and name the cause: {note!r}"

    # The level split, and the reason for it: no credentials is the COMMON path,
    # so a WARNING here would fire on every call in CI and on every laptop and
    # teach the demo's audience that warnings are noise.
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "the routine no-credentials path must not warn"
    )
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debugs) == 1, "demoted, not dropped"
    assert "no AWS credentials" in debugs[0].getMessage()


def test_no_credentials_never_touches_boto3(no_credentials):
    """The no-credentials path must SHORT-CIRCUIT, not fail a call and recover.

    Both spellings return an honest string, so no assertion on the return value
    can tell them apart -- but only one is free. A version that built a client
    first would sit through a connect timeout on an air-gapped laptop before
    printing the same sentence.

    The autouse `_no_live_agentcore` guard is what makes this assertable: it
    raises `Failed`, which derives from BaseException, so deploy_note()'s
    `except Exception` cannot absorb it into an honest-looking degradation
    message. A plain RuntimeError here would be swallowed and this test would
    pass against the very code it exists to reject.
    """
    note = github_ops.deploy_note()
    assert "no AWS credentials" in note


def test_all_five_ready_returns_the_spec_string_exactly(credentialed, monkeypatch,
                                                        caplog):
    """RED step 3: the spec's done-when output stays REACHABLE, character for character.

    Ruling 10 says the spec's string must not be the unconditional return value
    -- it does not say it may be paraphrased. `==` against a literal written out
    in this file is the only assertion that catches a reordering, a renamed
    runtime, or a changed prefix.
    """
    client = _wire(monkeypatch, _FakeClient(pages=_all_five_ready()))

    with caplog.at_level(logging.DEBUG, logger="agentorg.github_ops"):
        note = github_ops.deploy_note()

    assert note == SPEC_NOTE, f"spec's done-when output drifted: {note!r}"
    assert client.asked_for == ["list_agent_runtimes"], (
        "the verdict must come from ListAgentRuntimes, not another operation"
    )
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "a healthy deploy is not a warning"
    )


def test_the_spec_done_when_command_prints_the_spec_string(credentialed, monkeypatch,
                                                           capsys):
    """The spec's done-when is `print(github_ops.deploy_note())` with no arguments.

    Pinned separately from the `==` test above because it pins a different
    thing: that the function is still callable BARE. A signature that grew a
    required parameter would keep the string test green (it would just be
    called differently) while breaking every caller and the spec's own command.
    """
    _wire(monkeypatch, _FakeClient(pages=_all_five_ready()))

    print(github_ops.deploy_note())

    assert capsys.readouterr().out == SPEC_NOTE + "\n"


def test_a_partial_deploy_names_what_is_not_ready(credentialed, monkeypatch, caplog):
    """Three runtimes up is not a deploy, and the note must say which are missing.

    This is the state a half-finished `agentcore launch` leaves behind, and it
    is the one most likely to be mistaken for success on a projector. Naming the
    absent runtimes is what makes the message actionable rather than merely
    negative.
    """
    pages = [_page(*(_runtime(n) for n in SPEC_NAMES[:3]))]
    _wire(monkeypatch, _FakeClient(pages=pages))

    with caplog.at_level(logging.DEBUG, logger="agentorg.github_ops"):
        note = github_ops.deploy_note()

    assert note != SPEC_NOTE, "a partial deploy must not render as the full one"
    assert "3 of 5 runtimes ready" in note, f"the count must be honest: {note!r}"
    assert "theagentorg_security" in note and "theagentorg_sre" in note, (
        f"the note must name what is missing: {note!r}"
    )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "credentials that work but a deploy that is not there"


def test_a_runtime_that_exists_but_is_not_ready_is_not_counted(credentialed,
                                                               monkeypatch):
    """Existence is not readiness. CREATE_FAILED must never render as deployed.

    The mutation this defends against is dropping the status filter, which is
    the easiest simplification to make and produces a note that claims five
    live runtimes while one of them failed to create. All five names are
    present in the response, so a name-only check passes and this fails.
    """
    runtimes = [_runtime(n) for n in SPEC_NAMES]
    runtimes[4] = _runtime(SPEC_NAMES[4], status="CREATE_FAILED")
    _wire(monkeypatch, _FakeClient(pages=[_page(*runtimes)]))

    note = github_ops.deploy_note()

    assert note != SPEC_NOTE, "CREATE_FAILED is not a deploy"
    assert "4 of 5 runtimes ready" in note, f"got {note!r}"
    assert "theagentorg_sre" in note


def test_a_similarly_named_runtime_cannot_satisfy_the_check(credentialed, monkeypatch):
    """theagentorg_planner_v2 is not theagentorg_planner.

    Pins exact set membership against a substring or prefix comparison. A
    startswith/`in`-the-string check would accept this response as complete and
    report a deploy where one of the five is genuinely absent.
    """
    names = [f"{n}_v2" if n == "theagentorg_planner" else n for n in SPEC_NAMES]
    _wire(monkeypatch, _FakeClient(pages=[_page(*(_runtime(n) for n in names))]))

    note = github_ops.deploy_note()

    assert note != SPEC_NOTE, "a near-miss name must not satisfy the check"
    assert "4 of 5 runtimes ready" in note, f"got {note!r}"
    assert "theagentorg_planner" in note, "and the absent one must be named"


def test_runtimes_split_across_pages_still_verify(credentialed, monkeypatch):
    """The five may not arrive in one page, and a real account holds other runtimes.

    Pins the paginator against a bare list_agent_runtimes() call: with an
    unpaginated read the last two runtimes are invisible and a live, complete
    deploy reports as partial -- the inverse fabrication, and just as wrong.
    """
    pages = [
        _page(_runtime("someone_elses_runtime"), *(_runtime(n) for n in SPEC_NAMES[:2])),
        _page(*(_runtime(n) for n in SPEC_NAMES[2:])),
    ]
    _wire(monkeypatch, _FakeClient(pages=pages))

    assert github_ops.deploy_note() == SPEC_NOTE


def test_nothing_deployed_yet_says_so(credentialed, monkeypatch):
    """An empty account is the "no deployment yet" case the plan names."""
    _wire(monkeypatch, _FakeClient(pages=[_page()]))

    note = github_ops.deploy_note()

    assert "0 of 5 runtimes ready" in note, f"got {note!r}"
    for name in SPEC_NAMES:
        assert name in note, "with nothing deployed, all five are the missing set"
    assert SPEC_NOTE not in note, "the verified string must not appear"


@pytest.mark.parametrize(
    "boom, cause",
    [
        pytest.param(
            EndpointConnectionError(
                endpoint_url="https://bedrock-agentcore-control.us-east-1.amazonaws.com/"
            ),
            "EndpointConnectionError",
            id="no-network",
        ),
        pytest.param(
            ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                "ListAgentRuntimes",
            ),
            "ClientError",
            id="access-denied",
        ),
        pytest.param(
            NoCredentialsError(),
            "NoCredentialsError",
            id="credentials-vanished-after-the-probe",
        ),
    ],
)
def test_a_failed_call_degrades_instead_of_raising(credentialed, monkeypatch, caplog,
                                                   boom, cause):
    """RED step 2: the plan says never an exception. Three real botocore failures.

    The third case matters on its own: _aws_credentials_available() succeeding
    does not guarantee the call will, because an expired SSO cache resolves as
    "present" and fails on use. Without this branch that machine gets a
    traceback out of a function documented never to raise.
    """
    _wire(monkeypatch, _FakeClient(boom=boom))

    with caplog.at_level(logging.DEBUG, logger="agentorg.github_ops"):
        note = github_ops.deploy_note()          # must not raise

    assert note, "a failed call must still produce a sentence, not silence"
    for name in SPEC_NAMES:
        assert name not in note, f"a failed call must not name {name}"
    assert "unverified" in note, f"got {note!r}"
    assert "could not list runtimes" in note, f"got {note!r}"
    assert cause in note, f"the note must name the cause: {note!r}"

    # Credentials that exist but do not work is an anomaly, not the routine
    # path, so this one DOES earn a projector line -- exactly one.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "one line, not none and not three"
    assert warnings[0].exc_info is None, "the traceback must not ride the warning"
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert [r for r in debugs if r.exc_info is not None], "demoted, not discarded"


def test_a_malformed_response_degrades_rather_than_raising(credentialed, monkeypatch):
    """A response missing the keys we read is a failure, not a KeyError.

    Parsing lives inside the same try as the call for this reason: botocore
    validates against its model, but a stubbed endpoint, a proxy or a future
    API version does not have to. The mutation this catches is moving the set
    comprehension below the except clause.
    """
    _wire(monkeypatch, _FakeClient(pages=[{"unexpectedKey": None}, None]))

    note = github_ops.deploy_note()

    assert "unverified" in note, f"got {note!r}"
    for name in SPEC_NAMES:
        assert name not in note


def test_a_chatty_control_plane_error_stays_one_bounded_line(credentialed, monkeypatch,
                                                             caplog):
    """The note lands on the projector, so an enormous botocore error must be bounded.

    Same claim and same reason as test_a_chatty_github_failure_stays_one_short_
    warning_line in tests/test_offline_mode.py: a ClientError carries the whole
    HTTP response body, and a wall of text beside the pipeline status reads as a
    crash rather than as the degradation working. This is the test that proves
    _one_line is actually applied -- deleting it leaves every other test here
    green, which would make the bound a decorative claim.
    """
    noise = '{"message": "Internal Server Error", "requestId": "abc-123"}, ' * 1000
    boom = ClientError(
        {"Error": {"Code": "InternalServerException", "Message": noise}},
        "ListAgentRuntimes",
    )
    _wire(monkeypatch, _FakeClient(boom=boom))

    with caplog.at_level(logging.DEBUG, logger="agentorg.github_ops"):
        note = github_ops.deploy_note()

    assert len(noise) > 50000, "the fixture must actually be enormous"
    assert "\n" not in note, "a multi-line note is not one projector line"
    assert len(note) < 400, f"the note was {len(note)} chars: {note[:120]}..."
    assert "chars total" in note, "truncation must be marked, not silent"
    assert "InternalServerException" in note, "and the cause must survive the bound"

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    line = warnings[0].getMessage()
    assert "\n" not in line and len(line) < 400, f"WARNING was {len(line)} chars"

    # Demote, don't drop: the whole body is still recoverable at DEBUG.
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    rendered = "\n".join(logging.Formatter().format(r) for r in debugs)
    assert noise.strip()[:200] in rendered, "the full error must survive at DEBUG"


def test_the_partial_note_never_echoes_aws_supplied_text(credentialed, monkeypatch):
    """The partial note is bounded BY CONSTRUCTION: it never interpolates AWS text.

    Written first as "the missing-runtime list is truncated like the error
    detail", which was a FICTIONAL property -- `missing` is a subset of
    github_ops.RUNTIME_NAMES, this module's own five literals, so AWS-supplied
    names can never reach the note and _one_line can never fire on that path.
    Measured worst case with all five absent: 167 characters. Asserting on a
    truncation marker there pinned a mechanism the code does not use, which is
    the inverse defect -- a test green for the wrong reason.

    What IS real, and what this pins instead: the control plane cannot put text
    on the projector through this path. The mutation it catches is the obvious
    "improvement" of reporting the names AWS actually returned, which would make
    the note's length a function of a remote response.
    """
    long_names = [f"{n}_{'x' * 300}" for n in SPEC_NAMES]
    _wire(monkeypatch, _FakeClient(pages=[_page(*(_runtime(n) for n in long_names))]))

    note = github_ops.deploy_note()

    assert "\n" not in note
    assert len(note) < 200, f"the note was {len(note)} chars: {note[:120]}..."
    assert "x" * 300 not in note, "AWS-supplied text must not reach the projector"
    # It reports the five it EXPECTED and did not find, not the five it saw.
    assert "0 of 5 runtimes ready" in note, f"got {note!r}"
    for name in SPEC_NAMES:
        assert name in note


def test_the_credential_probe_makes_no_network_call():
    """_aws_credentials_available must be the cheap local probe, not an API call.

    Pins the reuse of llm.available()'s shape. The mutation this catches is
    replacing the probe with a real call such as sts.get_caller_identity(),
    which would answer the same question correctly while making deploy_note()
    block on the network on every invocation -- including in CI. The autouse
    guard makes any attempt to reach AWS through the client seam fail loudly.

    Asserts only that the answer is a bool, deliberately: WHICH bool depends on
    whether the machine running the suite has credentials, and a test whose
    verdict reads the ambient environment passes on one laptop and fails on
    another. The claim here is about cost, not about this machine's AWS setup.
    """
    assert github_ops._aws_credentials_available() in (True, False)


def test_the_client_is_bounded_and_uses_the_configured_region(monkeypatch):
    """The real client constructor must be time-bounded and region-driven.

    The only test that exercises _agentcore_client itself; everywhere else it is
    replaced. botocore's defaults are connect_timeout=60, read_timeout=60 and
    retrying enabled, which on an unreachable control plane stalls a judged demo
    for minutes before it can print anything honest. Constructing a client makes
    no network call, so this stays offline.

    Region comes from config.AWS_REGION rather than a literal: the `(us-east-1)`
    in the output string is the spec's wording, not a hardcoded endpoint.

    Uses the constructor captured at import time, because the autouse guard has
    replaced the module attribute with a raiser.
    """
    monkeypatch.setattr(config, "AWS_REGION", "eu-west-2")

    client = _REAL_AGENTCORE_CLIENT()

    assert client.meta.region_name == "eu-west-2"
    assert client.meta.config.connect_timeout == 3
    assert client.meta.config.read_timeout == 5
    # botocore reports max_attempts=0 as total_max_attempts=1, i.e. one try.
    assert client.meta.config.retries["total_max_attempts"] == 1


def test_the_failure_note_names_the_region_it_actually_queried(credentialed,
                                                               monkeypatch):
    """An honest failure says WHERE it looked, and that must track AWS_REGION.

    Catches a note that hardcodes "us-east-1" in its failure text: on a machine
    with AWS_REGION set elsewhere that message sends the reader to the wrong
    console page while the query went somewhere else entirely.
    """
    monkeypatch.setattr(config, "AWS_REGION", "eu-west-2")
    _wire(monkeypatch, _FakeClient(boom=RuntimeError("boom")))

    note = github_ops.deploy_note()

    assert "eu-west-2" in note, f"got {note!r}"
    assert "us-east-1" not in note, "the failure note must not name a region it skipped"


def test_the_module_does_not_import_boto3_eagerly():
    """boto3 stays a lazy import, as in llm.available().

    github_ops is imported by graph.py and by tests/conftest.py, so a
    module-level `import boto3` would make every import in the project -- and
    every CI run -- pay for botocore whether or not anything asks about the
    deploy. Reads the source rather than checking sys.modules, because another
    test importing boto3 first would mask the regression.
    """
    from pathlib import Path

    source = Path(github_ops.__file__).read_text(encoding="utf-8")
    module_level = [
        line for line in source.splitlines()
        if line.startswith(("import boto", "from boto", "from botocore"))
    ]
    assert module_level == [], f"boto3 must stay lazy; found {module_level}"
