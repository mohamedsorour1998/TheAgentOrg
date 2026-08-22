#!/usr/bin/env python
"""Is the deployed pipeline actually real? One command, four checks, exit 0 or 1.

Run this before a demo. Every check answers a question whose WRONG answer has
already happened once in this project, and every one of them reported green while
being wrong:

  1. Can the runtime role invoke the model the code asks for?
     -- MEASURED implicitDeny 2026-08-22. `config.BEDROCK_MODEL` is a cross-region
        INFERENCE PROFILE and the role granted foundation-model only, so
        bedrock:InvokeModel was denied, llm.text() caught it by design, and every
        model-calling agent served its fixture for a week. The deployed plan
        comment matched fixtures/plan_result.json byte for byte.

  2. Do the five runtimes exist and report READY?
     -- READY is NECESSARY AND NOT SUFFICIENT: a runtime reports READY before its
        endpoint serves the new version. Check 3 is the sufficient one.

  3. Does the security runtime return REAL scanner line numbers?
     -- {3, 4}, not the fixture's {4, 5}. THE ONLY FIELD THAT SEPARATES THEM.
        `blocking=2`, verdict `block`, both rule names, the file, the tool and the
        severity are produced identically by both paths, so a count proves
        nothing. The two sets overlap at line 4, so no single finding separates
        them either -- only the whole set does.

  4. Do the three Environments each have a required reviewer?
     -- An Environment with no required reviewer DOES NOT PAUSE. It runs. Before
        2026-08-22 gate1 had `protection_rules: []` and did exactly that.

Exits 1 on the first failure, with a message naming what to do about it. Prints
every check's evidence, so the output is the record rather than a claim about it.

WHAT THIS DELIBERATELY DOES NOT DO. It makes no writes: no dispatch, no apply, no
deploy. Check 3 invokes the security runtime, which costs model tokens and is the
only call here that is not read-only -- that is the price of the one check that
can distinguish a real scan from a fixture, and it writes nothing.

It is a CHECKED-IN SCRIPT rather than a heredoc in a workflow, for the ruling
ci.yml:202-206 already made: the bytes CI runs must be the bytes anyone can run,
and YAML indentation silently rewrites Python.

Usage:
    .venv-main/bin/python scripts/preflight.py
    .venv-main/bin/python scripts/preflight.py --runtime-prefix theagentorg_
    .venv-main/bin/python scripts/preflight.py --skip-invoke   # checks 1, 2, 4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentorg.common import config
from agentorg.state import DevResult, RunState

# THE LINE SETS ARE IMPORTED, NOT RESTATED. A hardcoded copy here would be a
# second declaration of the fact this repository's whole verification story rests
# on -- and the two copies would drift silently, because both would keep passing.
# tests/provenance.py also cross-checks the sets against shutil.which and RAISES
# when they disagree, which a copy could not do.
#
# `tests/` has no __init__.py; pyproject sets pythonpath = ["."], which makes this
# import work under pytest and under `python` from the repository root, but not
# from any other cwd. Hence the sys.path insert above.
#
# NO LINT-SUPPRESSION COMMENTS ANYWHERE IN THIS FILE, and none is needed: E402
# (module-level import not at top of file) is not in ruff 0.16's default set --
# verified, ruff reported three such directives as UNUSED when they were present.
# CLAUDE.md forbids them outright, so the absence is deliberate.
#
# (This note deliberately avoids writing the directive's literal spelling: ruff
# reads it even inside prose and warns that the comment is malformed.)
from tests.provenance import FIXTURE_LINES, REAL_SCANNER_LINES

ACCOUNT = "339712964409"
REGION = "us-east-1"
RUNTIME_ROLE = f"arn:aws:iam::{ACCOUNT}:role/theagentorg-shared-agentcore-runtime-role"
THIS_REPO = "mohamedsorour1998/TheAgentOrg"

AGENTS = ("planner", "developer", "reviewer", "security", "sre")
GATES = ("gate1", "gate2", "gate3")

# The rule type that makes an Environment PAUSE. GitHub's API spells it exactly
# this way; a branch-policy or wait-timer rule is not a human gate.
REVIEWER_RULE = "required_reviewers"

# The poisoned change check 3 sends is LOADED FROM THE FIXTURE, not written here,
# and that is measured rather than tidiness.
#
# MEASURED 2026-08-22 with a hand-written diff in this file that differed from the
# reference one only by a MISSING BLANK LINE among the added lines:
#
#   LINES: [2, 3]    expected: [3, 4]
#   provenance: scanners
#
# The scanners had genuinely run -- `provenance: scanners`, verdict `block`,
# `blocking=2` -- and the check still failed, correctly, because the line numbers
# are indices into the ADDED-LINES-ONLY file that common/diff.py materialises.
# Delete one blank line from the added set and every finding below it shifts up by
# one. So REAL_SCANNER_LINES is a property of {the scanners} AND {this exact diff},
# and any second copy of the diff is a second thing that has to stay byte-identical
# to keep the discriminator meaningful.
#
# `fixtures_loader.dev(poisoned=True)` is the same reference diff
# `developer._key_is_in_the_change`'s safety net substitutes, so this check
# exercises the same bytes the pipeline does.
def _poisoned_reference_diff() -> tuple[str, list[str]]:
    """The reference poisoned diff and its files, from the fixture."""
    from agentorg import fixtures_loader

    reference = fixtures_loader.dev(poisoned=True)
    return reference.diff, list(reference.files_changed)

_PASS = "PASS"
_FAIL = "FAIL"


class CheckFailed(Exception):
    """A check answered no. Carries the remedy, not just the symptom."""


def _say(line: str = "") -> None:
    print(line, flush=True)


def _result(number: int, name: str, verdict: str, detail: str) -> None:
    _say(f"[{verdict}] check {number}: {name}")
    for line in detail.splitlines():
        _say(f"        {line}")
    _say()


def _aws(*args: str) -> str:
    """Run an `aws` command and return stdout, raising CheckFailed on failure.

    Not boto3 for the IAM simulation deliberately: the command is the thing a
    reader will re-run by hand from this script's output, so the script runs the
    same command rather than a boto3 equivalent that happens to agree.
    """
    completed = subprocess.run(
        ["aws", *args], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise CheckFailed(
            f"`aws {' '.join(args)}` exited {completed.returncode}.\n"
            f"{completed.stderr.strip()}\n"
            f"If this is a credentials error, the rest of this script cannot run "
            f"either -- assume the role first."
        )
    return completed.stdout.strip()


# --------------------------------------------------------------------------
# Check 1 -- the IAM grant. The highest-value check here.
# --------------------------------------------------------------------------


def check_iam_can_invoke_the_model() -> str:
    """simulate-principal-policy for the ARN `config.BEDROCK_MODEL` actually names.

    SIMULATION, NOT A GREEN APPLY. A terraform apply proves the policy was
    WRITTEN; only this proves it PERMITS the call. Those were different facts for
    a week, and the difference was invisible.
    """
    model = config.BEDROCK_MODEL
    profile_arn = f"arn:aws:bedrock:{REGION}:{ACCOUNT}:inference-profile/{model}"

    decision = _aws(
        "iam", "simulate-principal-policy",
        "--policy-source-arn", RUNTIME_ROLE,
        "--action-names", "bedrock:InvokeModel",
        "--resource-arns", profile_arn,
        "--query", "EvaluationResults[0].EvalDecision",
        "--output", "text",
    )

    evidence = (
        f"role:     {RUNTIME_ROLE}\n"
        f"model:    {model}\n"
        f"resource: {profile_arn}\n"
        f"decision: {decision}"
    )

    if decision != "allowed":
        raise CheckFailed(
            f"{evidence}\n"
            f"\n"
            f"bedrock:InvokeModel is {decision!r} on the inference profile the code\n"
            f"asks for. EVERY MODEL-CALLING AGENT WILL SERVE ITS FIXTURE, silently,\n"
            f"with every job green -- llm.text() catches the denial by design.\n"
            f"\n"
            f"Remedy: the BedrockInvoke statement in\n"
            f"infra/Terraform/modules/agentcore/main.tf must grant BOTH ARN shapes --\n"
            f"the inference profile (the thing called) and foundation-model/* (the\n"
            f"things that answer). Either grant alone is still a denial. Then apply\n"
            f"through .github/workflows/terraform.yml and re-run this check; a green\n"
            f"apply is not the evidence, this simulation is."
        )

    # The profile fans out to foundation models, so the second grant matters too.
    fm_arn = f"arn:aws:bedrock:{REGION}::foundation-model/*"
    fm_decision = _aws(
        "iam", "simulate-principal-policy",
        "--policy-source-arn", RUNTIME_ROLE,
        "--action-names", "bedrock:InvokeModel",
        "--resource-arns", fm_arn,
        "--query", "EvaluationResults[0].EvalDecision",
        "--output", "text",
    )
    evidence += f"\nfoundation-model {fm_arn}: {fm_decision}"
    if fm_decision != "allowed":
        raise CheckFailed(
            f"{evidence}\n"
            f"\n"
            f"The inference profile is allowed but the FOUNDATION MODELS it routes to\n"
            f"are {fm_decision!r}. Invoking a cross-region profile needs InvokeModel on\n"
            f"both; with only the profile the call is still denied, and the symptom is\n"
            f"identical -- every agent serves its fixture and every job is green."
        )

    return evidence


# --------------------------------------------------------------------------
# Check 2 -- five runtimes, READY.
# --------------------------------------------------------------------------


def check_the_five_runtimes_are_ready(prefix: str) -> str:
    """Necessary, not sufficient. Check 3 is the sufficient one.

    Reads fields from the JSON response rather than scraping `--output text`,
    because that CLI appends a literal `None` line -- a trap that cost two failed
    deploy runs.
    """
    raw = _aws(
        "bedrock-agentcore-control", "list-agent-runtimes",
        "--output", "json",
    )
    runtimes = json.loads(raw).get("agentRuntimes", [])

    # EXACT name matching, not startswith on the agent name: `theagentorg_planner_v2`
    # must not satisfy `planner`, for the same reason agent_client matches exactly.
    found = {
        rt["agentRuntimeName"]: rt
        for rt in runtimes
        if rt.get("agentRuntimeName", "").startswith(prefix)
    }

    lines = []
    missing = []
    not_ready = []
    for agent in AGENTS:
        name = f"{prefix}{agent}"
        runtime = found.get(name)
        if runtime is None:
            missing.append(name)
            lines.append(f"{name:28} MISSING")
            continue
        status = runtime.get("status", "?")
        version = runtime.get("agentRuntimeVersion", "?")
        lines.append(f"{name:28} {status:8} v{version}")
        if status != "READY":
            not_ready.append(f"{name} is {status}")

    evidence = "\n".join(lines)

    if missing or not_ready:
        problems = []
        if missing:
            problems.append(f"{len(missing)} runtime(s) missing: {', '.join(missing)}")
        problems.extend(not_ready)
        raise CheckFailed(
            f"{evidence}\n"
            f"\n"
            + "\n".join(problems) + "\n"
            f"\n"
            f"Remedy: run .github/workflows/deploy.yml. If ALL five are missing, check\n"
            f"the prefix first -- this ran with --runtime-prefix {prefix!r}, and a\n"
            f"typo there reports the same symptom as a repository with no runtimes."
        )

    versions = {rt.get("agentRuntimeVersion") for rt in found.values()}
    if len(versions) > 1:
        raise CheckFailed(
            f"{evidence}\n"
            f"\n"
            f"The five runtimes are at DIFFERENT versions {sorted(versions)}. They are\n"
            f"built from one image, so a split version means a partial deploy: some\n"
            f"agents carry the new code and some do not, and no single stage's output\n"
            f"says which. Re-run deploy.yml."
        )

    return evidence


# --------------------------------------------------------------------------
# Check 3 -- the only check that separates a real scan from the fixture.
# --------------------------------------------------------------------------


def check_the_security_runtime_really_scans(prefix: str, force_fixture: bool) -> str:
    """Invoke the security runtime and compare the finding lines against the SETS.

    Compares whole SETS, never individual findings, because the two sets overlap
    at line 4 -- no single-line observation can separate the modes.

    `force_fixture` exists so this check can be PROVED TO FAIL without touching
    live infrastructure: it swaps the expected set for the fixture's, which a real
    scanner run cannot satisfy.
    """
    # Imported here rather than at module scope: agent_client constructs boto3
    # clients, and checks 1, 2 and 4 must be runnable when --skip-invoke is set.
    from agentorg.common import agent_client

    expected = FIXTURE_LINES if force_fixture else REAL_SCANNER_LINES
    label = "FIXTURE_LINES" if force_fixture else "REAL_SCANNER_LINES"

    diff, files_changed = _poisoned_reference_diff()
    state = RunState(ticket_id="PREFLIGHT-1", ticket_text="Add a per-IP login rate limit.")
    state.dev = DevResult(
        branch="preflight/scan-check",
        diff=diff,
        summary="preflight: a poisoned change, to observe which path answered",
        files_changed=files_changed,
    )

    original_remote = config.REMOTE_AGENTS
    config.REMOTE_AGENTS = True
    try:
        result = agent_client.call_agent("security", state)
    except Exception as exc:  # the classifier's message is the diagnosis
        logging_detail = f"{type(exc).__name__}: {exc}"
        raise CheckFailed(
            f"invoking {prefix}security raised.\n"
            f"{logging_detail}\n"
            f"\n"
            f"agent_client classifies its own failures -- read the message above\n"
            f"before assuming a deploy problem. A runtime reports READY before its\n"
            f"endpoint serves the new version, so a ResourceNotFoundException right\n"
            f"after a deploy is worth retrying; a denial is not."
        ) from exc
    finally:
        config.REMOTE_AGENTS = original_remote

    aws_rules = {"aws-access-key-id", "aws-secret-access-key"}
    blocking = list(getattr(result, "blocking", []) or [])
    lines = frozenset(f.line for f in blocking if f.rule in aws_rules)
    provenance = getattr(result, "scan_provenance", "") or "(unset)"

    evidence = (
        f"runtime:     {prefix}security\n"
        f"verdict:     {getattr(result, 'verdict', '?')}\n"
        f"blocking:    {len(blocking)}\n"
        f"LINES:       {sorted(lines)}\n"
        f"provenance:  {provenance}\n"
        f"expected:    {sorted(expected)}  ({label})\n"
        f"fixture set: {sorted(FIXTURE_LINES)}   real set: {sorted(REAL_SCANNER_LINES)}"
    )

    if not lines:
        raise CheckFailed(
            f"{evidence}\n"
            f"\n"
            f"The security runtime returned NO AWS-credential findings for a diff that\n"
            f"adds two of them. Provenance cannot be determined from findings that do\n"
            f"not exist -- so this is not 'the fixture answered', it is worse: either\n"
            f"the diff did not reach the scanners or the scanners read an empty tree."
        )

    if lines != expected:
        if force_fixture:
            # The inverted-expectation mode. Saying "NEITHER known set" here would
            # be wrong and confusing: the lines may be perfectly correct.
            raise CheckFailed(
                f"{evidence}\n"
                f"\n"
                f"Ran with --expect-fixture-lines, so this check REQUIRED the fixture's\n"
                f"{sorted(FIXTURE_LINES)} and got {sorted(lines)}. That is the intended\n"
                f"result on a healthy system: the flag exists only to prove this check\n"
                f"can fail, and a real scan cannot satisfy it. Drop the flag."
            )
        if lines == FIXTURE_LINES:
            raise CheckFailed(
                f"{evidence}\n"
                f"\n"
                f"THE FIXTURE ANSWERED. The finding lines are {sorted(lines)}, which is\n"
                f"fixtures/security_result_block.json's pair, not the scanners'.\n"
                f"\n"
                f"THE LINE NUMBERS ARE THE ONLY FIELD THAT SEPARATES THE TWO PATHS.\n"
                f"`blocking={len(blocking)}`, the verdict `block`, both rule names, the\n"
                f"file, the tool `gitleaks` and the severity `critical` are produced\n"
                f"IDENTICALLY by both, so none of them is evidence -- and the fixture's\n"
                f"explanation names a real file and a real remediation, so reading it\n"
                f"does not help either.\n"
                f"\n"
                f"THE BLOCK BEAT'S CENTRAL CLAIM IS CURRENTLY FALSE: the demo would show\n"
                f"a fixture verdict while asserting the scanners found the key.\n"
                f"\n"
                f"Remedy: check that the security image carries gitleaks, trivy and\n"
                f"semgrep (the image's build-time version tail proves they execute), and\n"
                f"that SCANNERS_REQUIRED=true is set on the security runtime and on no\n"
                f"other. `provenance: {provenance}` above is the container's own account\n"
                f"of which path it took -- if it says fixture-fallback, a scanner raised."
            )
        raise CheckFailed(
            f"{evidence}\n"
            f"\n"
            f"The finding lines are {sorted(lines)}, which matches NEITHER the real set\n"
            f"{sorted(REAL_SCANNER_LINES)} nor the fixture's {sorted(FIXTURE_LINES)}.\n"
            f"\n"
            f"The likeliest cause is NOT a broken scanner. Reported lines are indices\n"
            f"into the ADDED-LINES-ONLY file common/diff.py materialises, so any change\n"
            f"to the reference diff in fixtures/dev_result_poisoned.json shifts them --\n"
            f"MEASURED 2026-08-22: one missing blank line among the added lines moved\n"
            f"[3, 4] to [2, 3] while provenance still read `scanners` and the verdict\n"
            f"was still a correct block. Check the fixture before suspecting the\n"
            f"container.\n"
            f"\n"
            f"If the diff is unchanged, do NOT guess: re-measure both sets in\n"
            f"tests/provenance.py before trusting any metric built on this run."
        )

    return evidence


# --------------------------------------------------------------------------
# Check 4 -- an Environment with no reviewer does not pause. It runs.
# --------------------------------------------------------------------------


def check_the_gates_have_required_reviewers(repo: str) -> str:
    """Each of gate1/gate2/gate3 must carry a `required_reviewers` protection rule.

    Also REPORTS `can_admins_bypass`, without failing on it. That is an operator
    decision rather than a defect -- an admin can push a gate through without a
    reviewer click -- and it is printed so an honest answer exists if a judge asks
    whether a gate can be skipped. Failing on it would make this script refuse a
    configuration the team chose deliberately.
    """
    completed = subprocess.run(
        ["gh", "api", f"repos/{repo}/environments"],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise CheckFailed(
            f"`gh api repos/{repo}/environments` exited "
            f"{completed.returncode}.\n{completed.stderr.strip()}\n"
            f"This needs a `gh auth login` with access to {repo}."
        )

    environments = {
        env["name"]: env
        for env in json.loads(completed.stdout).get("environments", [])
    }

    lines = []
    problems = []
    bypassable = []
    for gate in GATES:
        env = environments.get(gate)
        if env is None:
            lines.append(f"{gate:8} MISSING")
            problems.append(f"{gate} does not exist")
            continue
        rules = [r.get("type") for r in env.get("protection_rules", [])]
        bypass = env.get("can_admins_bypass")
        lines.append(f"{gate:8} rules={rules} can_admins_bypass={bypass}")
        if REVIEWER_RULE not in rules:
            problems.append(f"{gate} has no {REVIEWER_RULE} rule")
        if bypass:
            bypassable.append(gate)

    evidence = "\n".join(lines)
    if bypassable:
        evidence += (
            f"\nNOTE: {', '.join(bypassable)} allow admins to bypass. Reported, not\n"
            f"failed -- an operator decision. The honest answer to \"can a gate be\n"
            f"skipped?\" is yes, by a repository admin, and it is recorded."
        )

    if problems:
        raise CheckFailed(
            f"{evidence}\n"
            f"\n"
            f"{'; '.join(problems)}\n"
            f"\n"
            f"AN ENVIRONMENT WITH NO REQUIRED REVIEWER DOES NOT PAUSE -- IT RUNS. So\n"
            f"the job goes green, the run continues, and nothing anywhere says a human\n"
            f"never saw it. Before 2026-08-22 gate1 had `protection_rules: []` and did\n"
            f"exactly that. Fix it in the repository's Environments settings; no edit\n"
            f"to run-pipeline.yml can substitute, which is the point of using\n"
            f"Environments for the gates."
        )

    return evidence


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prove the deployed pipeline is genuinely model-backed and "
                    "genuinely scanning. Exit 0 iff all checks pass.",
    )
    parser.add_argument(
        "--runtime-prefix", default="theagentorg_",
        help="runtime name prefix (default: theagentorg_). Point it at a "
             "nonexistent prefix to prove check 2 can fail.",
    )
    parser.add_argument(
        "--repo", default=THIS_REPO,
        help=f"repository whose Environments are checked (default: {THIS_REPO})",
    )
    parser.add_argument(
        "--skip-invoke", action="store_true",
        help="skip check 3. It is the only check that costs model tokens -- and "
             "the only one that separates a real scan from the fixture, so "
             "skipping it before a demo defeats the purpose.",
    )
    parser.add_argument(
        "--expect-fixture-lines", action="store_true",
        help="check 3 compares against FIXTURE_LINES instead of "
             "REAL_SCANNER_LINES. For proving check 3 can fail without touching "
             "live infrastructure; never correct on a healthy system.",
    )
    args = parser.parse_args(argv)

    _say("preflight -- is the deployed pipeline actually real?")
    _say(f"account {ACCOUNT} - region {REGION} - repo {args.repo}")
    _say()

    checks = [
        (1, "the runtime role can invoke the model the code asks for",
         lambda: check_iam_can_invoke_the_model()),
        (2, "five runtimes exist and report READY",
         lambda: check_the_five_runtimes_are_ready(args.runtime_prefix)),
    ]
    if not args.skip_invoke:
        checks.append(
            (3, "the security runtime returns REAL scanner line numbers",
             lambda: check_the_security_runtime_really_scans(
                 args.runtime_prefix, args.expect_fixture_lines)),
        )
    checks.append(
        (4, "the three Environments each require a reviewer",
         lambda: check_the_gates_have_required_reviewers(args.repo)),
    )

    for number, name, run in checks:
        try:
            _result(number, name, _PASS, run())
        except CheckFailed as failure:
            _result(number, name, _FAIL, str(failure))
            _say("preflight FAILED. The pipeline is not demo-ready.")
            return 1

    if args.skip_invoke:
        _say("check 3 was SKIPPED. Nothing here proved the scanners ran -- a")
        _say("fixture verdict and a real one are identical in every other field.")
    _say("preflight OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
