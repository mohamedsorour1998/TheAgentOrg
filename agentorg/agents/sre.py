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

from .. import fixtures_loader, github_ops
from ..common import llm
from ..state import RunState, SLOCheck, SREResult

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
    # MEASURED FIRST, so nothing below can be mistaken for it. Never raises;
    # returns exactly "passing", "failing" or "unknown".
    ci = github_ops.ci_status(state)

    advice = llm.structured(SREResult, SYSTEM_PROMPT, _prompt(state))
    if advice is None:
        # Stamped here for the reason planner.py's docstring gives: this suite
        # substitutes llm.structured, so llm's own recording never runs on the
        # path every offline run takes, and a run whose other four agents reached
        # the model would be labelled a model run with one fifth of it a fixture.
        llm.record_fixture_fallback()
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
