"""SRE agent — the final go/no-go, decided by CI and explained by the model.

OWNER: Sorour.  Strands agent on AgentCore.

WHY THE VERDICT IS NOT THE MODEL'S. This agent's verdict gates a merge, and the
premise of this whole pipeline is that the shipping decision is deterministic --
`compute_security_verdict` is five lines of pure Python for that reason. The same
reasoning applies one agent over: a model that is prompt-injected, or simply
having a bad day, must not be able to turn a red build into a deploy.

So:

    ci_status  <- github_ops.ci_status(state), a real GitHub API read
    verdict    <- code:  "no_go" if ci_status == "failing" else "go"
    slo_checks <- the measured CI check FIRST, then whatever the model contributes
    notes      <- the model's prose
    cost note  <- the model's prose

The model cannot reach `verdict` or `ci_status`. It cannot reach them INDIRECTLY
through `slo_checks` either: a model-authored check claiming `passed=False` is
recorded and does not flip the verdict, because that would be the same authority
by another route. Its checks are appended rather than merged, so a model check
named "CI" cannot displace the measured one -- which would otherwise put a
fabricated green CI line on the pull request beside a `no_go`.

The model's contribution is advisory in exactly the sense
`SecurityResult.explanation` is: real output, on the surface a human reads, with
no authority over the decision.

`unknown` yields `go`. A target repository with no CI still proceeds and the
honest `unknown` reaches the PR comment; whether that should block a MERGE is
`github_ops.merge_pr`'s decision, made there deliberately rather than smuggled in
as a side effect of this verdict.

BEFORE 2026-08-22 this module was a stub: it ignored its state, never imported
`llm`, and returned `fixtures/sre_result.json` -- `verdict: go`, `ci_status:
passing` -- whatever CI said. `SYSTEM_PROMPT` was written and never read. Since
this verdict now gates a merge, "merge when SRE says go" would have meant "always
merge".

As in planner.py there is deliberately no try/except around the model call.
`llm.structured` already absorbs every model-side failure and returns None, which
is the one signal this function acts on. `github_ops.ci_status` likewise never
raises, and its docstring is where that guarantee is recorded.
"""

from pydantic import BaseModel, Field

from .. import fixtures_loader, github_ops, repo_snapshot
from ..common import llm
from ..state import RunState, SLOCheck, SREResult


class SREAdvice(BaseModel):
    """THE THREE FIELDS THE MODEL IS ACTUALLY ASKED FOR, and only those.

    THIS EXISTS BECAUSE VALIDATING AGAINST `SREResult` COULD NOT SUCCEED. That model
    requires `verdict` and `ci_status` -- both strict Literals with no default -- and
    SYSTEM_PROMPT tells the model, correctly, that those two are not its to set. So a
    model that OBEYED the prompt produced a reply pydantic rejected for
    `Field required`, `llm.structured` collapsed the failure to None, and the fixture
    stood in. MEASURED on the deployed runtime, 3 calls out of 3:

        verdict=go ci=unknown source=fixture
        REJECTED: 2 validation errors for SREResult
        verdict     Field required
        ci_status   Field required

    The advice itself was good -- it named the new Redis dependency and the missing
    test for the rate-limiting logic -- and every word of it was discarded. The one
    observable difference was `_source=fixture` beside a stage whose measured half was
    plainly real, which is this project's signature defect wearing a new hat: a check
    that cannot distinguish "the model did not answer" from "the model answered and we
    threw it away".

    Narrowing the schema rather than loosening `SREResult` or softening the prompt:

      * defaults on `SREResult.verdict`/`ci_status` would make an absent verdict a
        valid one, and that model is the FROZEN contract every stage writes -- a
        default there would be read as a decision somewhere else in the pipeline
      * asking the model for both fields and then overwriting them invites it to
        reason about a verdict it does not control, and a `no_go` in a reply we
        discard is a fact nobody sees
      * this way the model literally cannot express the two fields it must not set,
        which is a stronger guarantee than dropping them after the fact
    """

    slo_checks: list[SLOCheck] = Field(default_factory=list)
    estimated_cost_note: str = ""
    notes: str = ""

SYSTEM_PROMPT = """You are the SRE reviewing a proposed change before deployment.

Return ONE JSON object matching the SREResult schema and nothing else.

TWO OF ITS FIELDS ARE NOT YOURS TO SET and will be overwritten: `verdict` and
`ci_status` are measured from the repository's real CI. Do not try to influence
them through an SLO check either -- a check you mark failed is recorded and
changes nothing.

Contribute:
  * slo_checks -- operational risks you can see in the diff, each with a name, a
    boolean and a one-line detail. Do not invent a CI check; one is added for you.
  * estimated_cost_note -- any new infrastructure or spend this change implies.
  * notes -- how to roll this change back, in one sentence.

Be brief and concrete."""

# The name of the check carrying the measured CI fact. A constant because
# tests/test_sre_agent.py looks for it and the pipeline's comment renders it --
# two readers, one definition.
CI_CHECK_NAME = "CI"


def _prompt(state: RunState) -> str:
    """What the model is shown. The diff is the point.

    Without the diff the model is asked for "risks you can see in the diff" and
    answers confidently about nothing -- the call happens, the reply parses, and
    the content is untethered. Same defect as the developer's revision prompt
    losing the diff it was meant to revise.
    """
    parts = [f"TICKET:\n{state.ticket_text}"]
    if state.dev is not None:
        parts.append(f"CHANGE SUMMARY:\n{state.dev.summary}")
        parts.append("FILES CHANGED:\n" + (", ".join(state.dev.files_changed)
                                           or "(none)"))
        parts.append(f"DIFF:\n{state.dev.diff}")

    # THE REPOSITORY, so "operational risk" is judged against what is actually
    # deployed rather than against a generic web service. The same snapshot every
    # other agent reads.
    context = repo_snapshot.render(
        state.dev.files_changed if state.dev is not None else None
    )
    if context:
        parts.append(context)
    if state.security is not None:
        parts.append(
            f"SECURITY VERDICT: {state.security.verdict} "
            f"({len(state.security.blocking)} blocking finding(s))"
        )
    return "\n\n".join(parts)


def run(state: RunState) -> SREResult:
    """Real CI decides; the model advises. See the module docstring.

    Returns an SREResult whose `verdict` and `ci_status` came from code, and whose
    `slo_checks`, `notes` and `estimated_cost_note` carry the model's advice with
    the measured CI check in front of it.
    """
    # THE MEASUREMENT, PREFERRED FROM THE STATE. Never raises; always one of the
    # three. See `RunState.ci_status_measured` for why it cannot always be taken here:
    # under REMOTE_AGENTS=true this body runs inside a container with no GitHub token,
    # so `ci_status` would answer "unknown" on its first line without asking GitHub --
    # measured on the clean demo run, whose checks were green 49 seconds earlier.
    #
    # `or` is right rather than a falsy-value trap: the field is `""` exactly when
    # nobody measured, and every real answer ("passing"/"failing"/"unknown") is
    # truthy. So a blank means measure it, and "unknown" measured on the runner is
    # carried through as the real answer it is.
    ci = state.ci_status_measured or github_ops.ci_status(state)

    # `SREAdvice`, NOT `SREResult` -- see that class for the measurement. Asking for
    # the wide model here required two fields the prompt forbids, so every obedient
    # reply was rejected and the fixture served instead, on every call.
    advice = llm.structured(SREAdvice, SYSTEM_PROMPT, _prompt(state))
    if advice is None:
        # Stamped here for the reason planner.py's docstring gives: this suite
        # substitutes llm.structured, so llm's own recording never runs on the
        # path every offline run takes, and a run whose other four agents reached
        # the model would be labelled a model run with one fifth of it a fixture.
        llm.record_fixture_fallback()
        # The fixture is an SREResult; only its three advisory fields are read, so
        # its `verdict: go` / `ci_status: passing` cannot reach the return value
        # below any more than the model's could.
        advice = fixtures_loader.sre()

    # `passed` tracks the measurement and nothing else. NOT `ci != "failing"`:
    # `unknown` means nothing was examined, so nothing passed, and a check
    # claiming otherwise would put a green CI line on the pull request for a
    # repository that has never run a test. The verdict below treats `unknown`
    # differently on purpose -- these are two different questions.
    ci_check = SLOCheck(
        name=CI_CHECK_NAME,
        passed=(ci == "passing"),
        detail=f"CI reports {ci} for this change's head commit",
    )

    # The measured check goes FIRST so a reader sees the fact the verdict rests
    # on before any advice, and the model's checks are APPENDED rather than
    # merged by name -- a model check called "CI" must not be able to displace
    # the measured one. The model's own `verdict` and `ci_status` are dropped on
    # the floor, which is the whole point.
    return SREResult(
        verdict="no_go" if ci == "failing" else "go",
        ci_status=ci,
        slo_checks=[ci_check, *advice.slo_checks],
        estimated_cost_note=advice.estimated_cost_note,
        notes=advice.notes,
    )
