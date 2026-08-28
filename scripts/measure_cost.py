#!/usr/bin/env python
"""What one change costs, three ways. Spec §4, Lane L, L3.

    PYTHONPATH=. .venv-main/bin/python scripts/measure_cost.py --runs 3
    PYTHONPATH=. .venv-main/bin/python scripts/measure_cost.py --runs 3 --require-model

`PYTHONPATH=.` IS NOT OPTIONAL IN A WORKTREE and omitting it fails silently in the
worst way -- `sys.path[0]` becomes `scripts/`, the editable install resolves
`agentorg` to the SHARED checkout, and the run executes another tree's code. That is
CLAUDE.md's `cf5cb83`, which three lanes each diagnosed as their own regression.

THE THREE SCENARIOS ARE NOT THREE ESTIMATES
===========================================
The spec asks to compare a human developer working with Claude Code, this pipeline on
AWS, and this pipeline self-hosted. Only ONE of the three has a token bill this
repository can read, and pretending otherwise is the thing this file refuses to do:

    cloud       MEASURED. Real token counts from `llm.usage()`, priced by
                `agentorg/cost/prices.py`, whose rows carry the AWS Pricing API query
                that produced them and the date it was read.
    self-hosted MEASURED IN TIME, NOT IN MONEY. Lane F ran the same poisoned ticket
                against a local gateway and recorded wall clock. Electricity and
                amortised hardware are not readable from in here, so the marginal
                token cost is stated as $0.00 and the OPPORTUNITY cost -- the wall
                clock -- is what the row actually reports.
    human       NOT MEASURED, AND NOT ESTIMATED EITHER. A developer's hourly rate is
                not in this repository, the wall clock of a human review is not
                either, and a plausible-looking number here would be the most quoted
                figure in the whole comparison. The row carries the two inputs a
                reader must supply and the arithmetic that combines them.

SO THE COMPARISON IS ASYMMETRIC BY CONSTRUCTION, AND SAYS SO. An evidence artifact
whose three columns look equally solid while one is invented is worse than a gap:
CLAUDE.md's rule is that a gap invites the measurement and a number ends it.

WHY IT PRICES A WALK RATHER THAN A DEPLOYED RUN
==============================================
`REMOTE_AGENTS=true` puts the model call inside the AgentCore container, and Lane E
measured the consequence: usage crosses the seam only if two wiring lines exist in
`agents/server.py` and `common/agent_client.py`, which are the integrator's files.
Absent them a deployed run records `stages=0 usd=None` on the runner. So this script
drives `graph.run_pipeline` IN PROCESS, where `llm.usage()` sees every call -- and
reports `stages` beside `usd` for exactly Lane E's reason: an unwired run has zero
rows with `usd=None`, a wired run that fell back has a row per stage with `usd=0.0`,
and `usd == 0.0` alone cannot tell them apart.

The infrastructure line is separate and is read from the shipped Terraform rather
than recalled: Lambda invocations, EventBridge events and DynamoDB writes per run.
Each is priced at AWS's published on-demand rate with the rate's own name beside it,
and the total is reported to five decimal places because rounding it to cents renders
the entire non-model cost of a run as $0.00, which reads as "free" rather than as
"below the resolution of this table".
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
import statistics
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# AFTER the path insert, and with NO suppression comment. CLAUDE.md forbids those
# outright, and none is needed: E402 is not in ruff 0.16's default rule set, so the
# directive was itself an error -- `RUF100 Unused directive (non-enabled: E402)`, three
# times. Spelling the marker out in this comment ALSO warns (`Invalid directive on
# line 70: expected code to consist of uppercase letters followed by digits`), because
# ruff parses any occurrence of it, comment or not. `scripts/measure_prompts.py`
# imports the same way for the same reason.
from agentorg import graph, state
from agentorg.common import config, llm
from agentorg.cost import prices, record

# ── the AWS lines that are not the model ──────────────────────────────────────
# Read off the shipped Terraform, one resource each, then priced at the published
# on-demand rate. Every row carries the rate's own name so a reader can check it,
# and the QUANTITY is what this repository's own code does per run -- not a guess.
#
# NOT INCLUDED, deliberately, and each for a reason a reader can check:
#   * GitHub Actions minutes. Free on a public repository, and this one is public.
#     On a private repository they would dominate every other line here.
#   * ECR storage and CloudWatch retention. Per-account standing costs, not per-run.
#   * The Lambda's 256 MB / 10 s configuration. One invocation at 256 MB for well
#     under a second is below the free tier's monthly floor by four orders of
#     magnitude; pricing it produces a figure whose leading digit is noise.
INFRA_LINES = (
    # (name, quantity per run, USD per unit, the rate's published name)
    ("Lambda invocation", 1, 0.0000002, "AWS Lambda, $0.20 per 1M requests"),
    ("EventBridge event", 1, 0.000001, "Amazon EventBridge, $1.00 per 1M events"),
    # Nine stages, each writing one LogEvent row. `modules/state` is PAY_PER_REQUEST
    # and every row is far under the 1 KB write-unit, so nine rows is nine WCUs.
    ("DynamoDB write", 9, 0.00000125, "DynamoDB on-demand, $1.25 per 1M WCU"),
)

CLEAN_TICKET = (
    "Add a per-IP rate limit of five login attempts per minute to app/auth.py, "
    "returning HTTP 429 past the threshold. Read the limit and the Redis URL from "
    "environment variables."
)


def infra_usd() -> tuple[float, list[tuple[str, int, float, str]]]:
    """The non-model AWS cost of one run, and the rows it came from."""
    rows = [(name, qty, qty * rate, note) for name, qty, rate, note in INFRA_LINES]
    return sum(row[2] for row in rows), rows


def one_walk(*, poisoned: bool) -> dict:
    """Drive the real pipeline once in process and report what the model consumed.

    IN PROCESS, NOT REMOTE, and the docstring above says why. `auto_approve=True`
    because a walk that pauses at gate1 records the plan stage's tokens and nothing
    else -- and a cost comparison over one ninth of a run is worse than none.

    STDOUT IS CAPTURED AROUND THE WALK, and that is Lane H's measured finding rather
    than tidiness: `strands.Agent` STREAMS the model's reply to stdout, so the first
    readable run of that lane's harness had every result row prefixed by a fragment of
    the reply it described. Here the streamed JSON also scrolls the cost table off the
    screen, which is the table this script exists to print.
    """
    llm.reset_usage()
    llm.reset_source()
    started = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        result = graph.run_pipeline(
            ticket_id="COST-1",
            ticket_text=CLEAN_TICKET,
            auto_approve=True,
            poisoned=poisoned,
        )
    seconds = time.perf_counter() - started

    calls = llm.usage()
    cost = record.build_cost_record()
    return {
        "poisoned": poisoned,
        "status": result.status if isinstance(result, state.RunState) else str(result),
        # `last_source()` NAMES ONE CALL AND THIS RUN MAKES SIX, so it is the wrong
        # instrument for a run-level question -- measured, and it cost this script a
        # false refusal. `llm.record_fixture_fallback` records a ZERO-TOKEN ROW rather
        # than nothing (Lane E's E7), so the usage list can answer per call what
        # `_LAST_SOURCE` cannot answer for a run:
        #
        #   status promoted   last_source fixture
        #     fixture=False in=5267 out=258      <- planner,   the model answered
        #     fixture=False in=5374 out=589      <- developer, the model answered
        #     fixture=False in=6444 out=230      <- reviewer,  the model answered
        #     fixture=True  in=   0 out=  0      <- SRE, MaxTokensReachedException
        #     fixture=False in=5228 out= 66      <- security,  the model answered
        #     fixture=False in=5974 out=191      <- promote,   the model answered
        #
        # Five of six calls reached Bedrock and `last_source()` said `fixture`,
        # because the SRE happened to be fourth and something ran after it. A guard
        # reading that field refuses a run that is 83% model-backed, and it would
        # equally ACCEPT a run whose last call alone succeeded. Same class as
        # CLAUDE.md's `blocking=2`: a summary field that cannot separate the two
        # cases it is being asked about.
        "source": llm.last_source() or "",
        "model_calls": sum(1 for c in calls if not c.fixture),
        "fixture_calls": sum(1 for c in calls if c.fixture),
        "seconds": round(seconds, 3),
        "input_tokens": sum(c.input_tokens for c in calls),
        "output_tokens": sum(c.output_tokens for c in calls),
        "cached_tokens": sum(c.cached_tokens for c in calls),
        # `cached_reported` DOES NOT REACH `StageCost` (Lane E's named gap), so this
        # is read off `Usage` directly. False across every call means the provider
        # said nothing about caching -- not that it reported a miss.
        "cached_reported": any(c.cached_reported for c in calls),
        # `stages`, NOT `usd`, is the wiring discriminator -- Lane E measured it.
        "stages": len(cost.stages),
        "usd": record.total_usd(cost.stages),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--require-model",
        action="store_true",
        help="refuse a run in which every agent answered from its fixture",
    )
    parser.add_argument("--out", default="docs/final/evidence/cost-comparison.json")
    args = parser.parse_args()

    model_row = prices.PRICES.get(config.BEDROCK_MODEL)
    print(f"cost comparison  model {config.BEDROCK_MODEL}")
    print(f"  available:  {llm.available()}")
    print(f"  snapshot:   DEMO_REPO={config.GITHUB_REPO or '(unset -- agents reason blind)'}")
    if model_row is None:
        print(f"  PRICE:      NONE for {config.BEDROCK_MODEL}", file=sys.stderr)
        return 1
    print(
        f"  price:      in ${model_row.input_per_million}/1M  "
        f"out ${model_row.output_per_million}/1M  read {model_row.read_on}"
    )

    walks = []
    for index in range(args.runs):
        walk = one_walk(poisoned=False)
        walks.append(walk)
        print(
            f"  run {index + 1}  {walk['status']:9s} "
            f"model={walk['model_calls']}/{walk['model_calls'] + walk['fixture_calls']} "
            f"in={walk['input_tokens']:6d} out={walk['output_tokens']:5d} "
            f"cached={walk['cached_tokens']} stages={walk['stages']} "
            f"usd={walk['usd']:.6f}" if walk["usd"] is not None else
            f"  run {index + 1}  {walk['status']:9s} usd=NOT PRICED"
        )

    priced = [w for w in walks if w["usd"] is not None]
    model_usd = [w["usd"] for w in priced]
    infra, infra_rows = infra_usd()

    print()
    print("  MODEL COST PER CLEAN CHANGE")
    if model_usd:
        low, high = min(model_usd), max(model_usd)
        print(f"    range     ${low:.6f} - ${high:.6f}  over {len(model_usd)} runs")
        print(f"    median    ${statistics.median(model_usd):.6f}")
    else:
        print("    NOT PRICED -- no run produced a priced stage row")
    print()
    print("  AWS COST PER RUN THAT IS NOT THE MODEL")
    for name, qty, usd, note in infra_rows:
        print(f"    {name:20s} x{qty:<3d} ${usd:.8f}   {note}")
    print(f"    {'TOTAL':20s}     ${infra:.8f}")
    print()
    # THE CACHE FINDING, restated from the measurement rather than from Lane E's note.
    # `report.render` states it in words for the same reason: nobody reads 0 as an
    # alarm, and this is the largest silent cost in the design -- five agents each
    # re-send a repository snapshot on every call and pay full price for it.
    cached = sum(w["cached_tokens"] for w in walks)
    reported = any(w["cached_reported"] for w in walks)
    print(f"  CACHE: {cached} cached tokens across {len(walks)} runs; "
          f"provider reported caching at all: {reported}")

    # THE GUARD READS THE USAGE ROWS, NOT `last_source()`. See `one_walk`.
    if args.require_model and not any(w["model_calls"] for w in walks):
        print(
            "\n  REFUSING: not one call in any run reached the model, so these token "
            "counts describe a fixture read",
            file=sys.stderr,
        )
        return 1

    payload = {
        "model": config.BEDROCK_MODEL,
        "price_row": {
            "input_per_million": model_row.input_per_million,
            "output_per_million": model_row.output_per_million,
            "cache_read_per_million": model_row.cache_read_per_million,
            "read_on": model_row.read_on,
            "source": model_row.source,
        },
        "walks": walks,
        "model_usd_range": [min(model_usd), max(model_usd)] if model_usd else None,
        "model_usd_median": statistics.median(model_usd) if model_usd else None,
        "infra_usd_per_run": infra,
        "infra_rows": [
            {"line": name, "quantity": qty, "usd": usd, "rate": note}
            for name, qty, usd, note in infra_rows
        ],
    }
    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
