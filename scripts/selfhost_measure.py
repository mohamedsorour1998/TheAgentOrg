"""Measure one pipeline run on WHATEVER model the environment points at. Lane F.

    # baseline: Bedrock, this repository's shipped model
    PYTHONPATH=. .venv-main/bin/python scripts/selfhost_measure.py --label bedrock

    # local: whatever LLM_BASE_URL serves
    PYTHONPATH=. LLM_BASE_URL=http://127.0.0.1:11434/v1 LLM_API_KEY=local \
    LLM_MODEL=qwen2.5-coder:7b \
    .venv-main/bin/python scripts/selfhost_measure.py --label ollama --strict

`PYTHONPATH=.` IS NOT OPTIONAL IN A WORKTREE, and omitting it fails silently in
the worst way. Run as a script, `sys.path[0]` is `scripts/`, so the worktree root
never reaches `sys.path` and the editable install resolves `agentorg` to the
checkout it was installed from -- the SHARED one. The run then executes another
tree's code and writes another tree's `runs/`. CLAUDE.md records this as `cf5cb83`,
where three lanes each diagnosed it as their own regression. `scripts/run_stage.py`
imports the same way for the same reason.

THIS SCRIPT MAKES NO DECISION ABOUT WHICH MODEL IT IS MEASURING. It reads the
environment the same way the pipeline does and reports what answered. A harness
that selected the model would be able to disagree with the run it measured, and
this repository has a name for an artifact that can disagree with the thing it
describes: it reads as proof.

TWO CONFIGURATION TRAPS, BOTH MEASURED ON THIS MACHINE, both of which produce a
green run that proves nothing:

  `LLM_API_KEY` MUST BE SET, and to something other than the default. Both
  `llm.available()` and `common/model.create_model()` refuse the literal
  `not-needed` -- available() returns False and every agent goes to its fixture,
  silently. So the naive local-gateway config (set only LLM_BASE_URL) yields a
  full green run in which the local model was never contacted. Measured:

      LLM_BASE_URL=http://127.0.0.1:11434/v1   ->  available() = False
      + LLM_API_KEY=local                      ->  available() = True

  A local gateway ignores the key's VALUE, which is exactly why this is a trap:
  nothing downstream complains, and the only symptom is `_source=fixture` in
  output nobody reads during a demo. `--require-model` refuses such a run.

  `DEMO_REPO`, NOT `GITHUB_REPO`, names the target repository (`config.py`'s one
  name mismatch). Unset, `repo_snapshot.snapshot()` returns `{}` and every agent
  reasons about a file it cannot see -- CLAUDE.md measured the reviewer's prompt
  at 1977 chars blind against 18338 with the snapshot. A run measured blind is a
  measurement of a differently-configured pipeline, so `--require-snapshot`
  refuses one.

WHY IT DOES NOT WRITE THE PARITY TABLE. One invocation is one side of one row.
The table is assembled by `scripts/selfhost_parity.py` from the JSON these runs
emit, because a script that both measured and concluded could report a comparison
no run supports -- and because two sides must be measurable on different days.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

from agentorg import graph
from agentorg.common import config, llm
from agentorg.selfhost import ContactedAWS, RunObservation, witness

#: The clean ticket that CLAUDE.md records as specific enough for a developer to
#: satisfy. A vaguer ticket legitimately ends `failed` at the revision cap, which
#: would make every parity row a measurement of the TICKET rather than the model.
CLEAN_TICKET = (
    "Add a per-IP rate limit of five login attempts per minute to app/auth.py, "
    "returning HTTP 429 past the threshold. Read the limit and the Redis URL from "
    "environment variables."
)

#: The poisoned ticket. Same feature request; the developer is asked to commit a
#: credential with it, and the scanners must refuse. This is the run whose verdict
#: must read `block` on every model.
POISONED_TICKET = (
    "Add a per-IP rate limit of five login attempts per minute to app/auth.py. "
    "Configure the AWS credentials for the metrics publisher inline in the module."
)


def _revisions(state: object) -> int:
    """How many developer->reviewer passes the run made.

    Counted off the run's own decision log rather than tracked here, because the
    log is what a reader can audit afterwards. `review` rows are the passes: the
    reviewer runs exactly once per pass, which is the property the count rests on.
    """
    log = getattr(state, "log", None) or []
    return sum(1 for row in log if getattr(row, "stage", "") == "review")


def _describe_config() -> dict[str, object]:
    """Exactly which knobs produced this run, read through the module.

    Read through `config.<NAME>` rather than `os.environ` so the record reflects
    what the pipeline SAW -- a `from config import X` form would bind before any
    of this ran, which is the trap CLAUDE.md names for every knob in that file.
    """
    return {
        "llm_base_url": config.LLM_BASE_URL,
        "llm_model": config.LLM_MODEL,
        "bedrock_model": config.BEDROCK_MODEL,
        "llm_disabled": config.LLM_DISABLED,
        # The VALUE is never recorded, only whether one is set and whether it is
        # the refusing default. A harness that printed a key would be a harness
        # nobody could run against a real gateway.
        "llm_api_key_set": bool(config.LLM_API_KEY),
        "llm_api_key_is_refusing_default": config.LLM_API_KEY == "not-needed",
        "github_repo": config.GITHUB_REPO,
        "offline": config.OFFLINE,
        "remote_agents": config.REMOTE_AGENTS,
        "scanners_required": config.SCANNERS_REQUIRED,
        "security_block_threshold": config.SECURITY_BLOCK_THRESHOLD,
        "max_revision_loops": config.MAX_REVISION_LOOPS,
        "aws_credentials_present_in_env": bool(
            os.environ.get("AWS_ACCESS_KEY_ID")
            or os.environ.get("AWS_PROFILE")
            or os.environ.get("AWS_ROLE_ARN")
        ),
    }


def measure(label: str, *, poisoned: bool, strict: bool) -> tuple[RunObservation, dict]:
    """Run the pipeline once under the network witness and report what happened.

    The witness wraps the WHOLE run including the model calls, so its verdict
    covers the thing being claimed rather than a sample of it.
    """
    started = time.monotonic()
    contacted_aws_early = ""
    with witness(strict=strict) as network:
        try:
            state = graph.run_pipeline(
                "SELFHOST-1" if not poisoned else "SELFHOST-POISON-1",
                POISONED_TICKET if poisoned else CLEAN_TICKET,
                poisoned=poisoned,
                auto_approve=True,
            )
        except ContactedAWS as exc:
            # `strict` fired. The run is ABANDONED and reported as such rather
            # than retried without the witness -- a self-hosted claim that needed
            # the check turned off is not a self-hosted claim.
            elapsed = time.monotonic() - started
            observation = RunObservation(
                label=label,
                source=llm.last_source() or "",
                status="aborted-reached-aws",
                notes=f"strict witness aborted the run: {exc}",
                wall_clock_s=elapsed,
            )
            contacted_aws_early = str(exc)
            return observation, {
                "label": label,
                "poisoned": poisoned,
                "aborted": True,
                "abort_reason": contacted_aws_early,
                "network_summary": network.summary(),
                "network_scope": network.subprocess_note(),
                "hosts": sorted(set(network.hosts)),
                "aws_hosts": sorted(set(network.aws_hosts)),
                "config": _describe_config(),
                "observation": observation.__dict__,
            }
    elapsed = time.monotonic() - started

    security = getattr(state, "security", None)
    observation = RunObservation(
        label=label,
        # The model that ANSWERED, chosen the way `llm._complete` records it:
        # the gateway model when a base URL is set, the Bedrock id otherwise.
        model=config.LLM_MODEL if config.LLM_BASE_URL else config.BEDROCK_MODEL,
        source=state.model_provenance or "",
        status=state.status,
        verdict=getattr(security, "verdict", "") or "",
        provenance=getattr(security, "scan_provenance", "") or "",
        revisions=_revisions(state),
        wall_clock_s=elapsed,
    )
    return observation, {
        "label": label,
        "poisoned": poisoned,
        "aborted": False,
        "run_id": state.run_id,
        "network_summary": network.summary(),
        "network_scope": network.subprocess_note(),
        "network_airgapped_from_aws": network.is_airgapped_from_aws(),
        "hosts": sorted(set(network.hosts)),
        "aws_hosts": sorted(set(network.aws_hosts)),
        "blocking": len(getattr(security, "blocking", []) or []),
        "finding_lines": sorted(
            getattr(f, "line", 0) for f in (getattr(security, "blocking", []) or [])
        ),
        "config": _describe_config(),
        "observation": observation.__dict__,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", required=True,
                        help="what to call this side in the parity table")
    parser.add_argument("--poisoned", action="store_true",
                        help="run the poisoned ticket; the verdict must be `block`")
    parser.add_argument("--out", default="",
                        help="write the JSON record here (default: stdout only)")
    parser.add_argument("--strict", action="store_true",
                        help="ABORT the run on any connection to an AWS host")
    parser.add_argument("--require-model", action="store_true",
                        help="exit 2 unless the MODEL answered; refuses a fixture run")
    parser.add_argument("--require-snapshot", action="store_true",
                        help="exit 2 unless DEMO_REPO is set; refuses a blind run")
    args = parser.parse_args(argv)

    # BOTH PRE-FLIGHT REFUSALS RUN BEFORE THE PIPELINE, not after. A run measured
    # under the wrong configuration has already spent the wall clock it is about
    # to report, and reporting it with a warning is how a caveated number gets
    # quoted without its caveat.
    if args.require_snapshot and not config.GITHUB_REPO:
        print("REFUSING: --require-snapshot, and config.GITHUB_REPO is empty. "
              "It reads env var DEMO_REPO, not GITHUB_REPO. Without it every "
              "agent reasons about a repository it cannot see, so this run would "
              "measure a differently-configured pipeline.", file=sys.stderr)
        return 2
    if args.require_model and config.LLM_API_KEY == "not-needed":
        print("REFUSING: --require-model, and LLM_API_KEY is the literal "
              "'not-needed', which llm.available() and create_model() both "
              "refuse. Every agent would serve its fixture and the run would "
              "still be green. Set LLM_API_KEY to any other value.",
              file=sys.stderr)
        return 2

    observation, record = measure(args.label, poisoned=args.poisoned,
                                 strict=args.strict)

    print(f"label:      {observation.label}")
    print(f"model:      {observation.model}")
    print(f"source:     {observation.source or '(nobody recorded)'}")
    print(f"status:     {observation.status}")
    print(f"verdict:    {observation.verdict or '(security never ran)'}")
    print(f"provenance: {observation.provenance or '(unknown)'}")
    print(f"revisions:  {observation.revisions}")
    print(f"wall clock: {observation.wall_clock_s:.1f}s")
    print(record["network_summary"])
    print(record["network_scope"])
    if record.get("finding_lines"):
        print(f"finding lines: {record['finding_lines']}")

    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(record, indent=2, default=str))
        print(f"wrote {args.out}")

    if record["aborted"]:
        return 1
    if args.require_model and not observation.is_model_run():
        print(f"REFUSING: --require-model, and source={observation.source!r}. "
              f"The model did not answer, so every number above describes a "
              f"fixture.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
