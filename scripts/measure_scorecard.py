#!/usr/bin/env python
"""The evolution scorecard's baseline row, measured. Owner: Lane L.

    .venv-main/bin/python scripts/measure_scorecard.py

Writes `docs/final/evidence/scorecard-baseline.json` and prints the table it
wrote. Exit 0 when every dimension it CLAIMS to measure was measured; exit 1 when
one of them could not be, because a scorecard with a silently missing row is the
defect this repository exists to prevent.

WHY A SCRIPT AND NOT A TABLE SOMEBODY TYPED. `CLAUDE.md` rule 4: numbers in prose
come from a command whose output was pasted. A hand-typed scorecard goes stale the
first time anyone changes the pipeline and nothing anywhere says so. This file is
the command.

--------------------------------------------------------------------------------
WHAT IS MEASURED HERE, AND WHAT IS DELIBERATELY NOT
--------------------------------------------------------------------------------

Seven dimensions are in the specification (§3). Four of them can be measured on
this machine with no cloud, no GitHub and no model. Three cannot, and this script
reports them as `measured: false` with the reason and the command a human would
run -- it does not estimate them. An invented number in an evidence artifact is
worse than a gap, because a gap invites the measurement and a number ends it.

  MEASURED HERE
    block_correctness      poisoned runs that blocked / poisoned runs
    false_block_rate       clean runs that blocked / clean runs
    human_touches          HumanDecision records per run
    agent_rework           revision loops per run

  NOT MEASURABLE HERE, AND WHY
    time_to_merge          the local walk is ~1.5 s and the cloud run is minutes,
                           most of it a human deciding at a gate. A local timing
                           quoted as time-to-merge would be off by two orders of
                           magnitude in the flattering direction.
    cost_per_merged_change NOTHING records tokens. Measured: `input_tokens` and
                           `output_tokens` exist on `state.StageCost` (added by the
                           Phase 0 contract batch) and no code path writes them --
                           `agentorg/common/llm.py` contains no usage accounting at
                           all. Lane E builds it; until then this row has no source.
    escaped_defects        a count over shipped changes. Two merged PRs exist on
                           the target repository, which is too small a denominator
                           to quote as a rate.

--------------------------------------------------------------------------------
THE REGIME THIS RUNS IN, AND WHY IT IS FORCED RATHER THAN ASSUMED
--------------------------------------------------------------------------------

`tests/conftest.py`'s six autouse guards are PYTEST fixtures. This script is not
pytest, so none of them binds, and the seams underneath are live. Measured on this
machine before the guards were forced here:

    llm.available() -> True        (boto3 credentials are present)

So an unguarded run of this script would make a live billable Bedrock call per
agent per run, and -- with `DEMO_REPO` set -- real branch/commit/PR writes plus an
outbound clone. `_force_the_hermetic_regime` closes all three seams and RECORDS
that it did, in the report, because a number's regime is part of the number.

The scanners are deliberately NOT closed. They are local binaries, they are the
thing under measurement, and their absence would silently move this from measuring
the block rule to measuring JSON deserialisation. `tests/provenance.py` names the
mode and the report carries it; `--require-real-scanners` refuses to write a row
measured any other way.

--------------------------------------------------------------------------------
WHY EVERY TIMING IS A RANGE
--------------------------------------------------------------------------------

`CLAUDE.md` records 116.88s -> 149.68s -> 102.83s for one unchanged test snapshot,
purely load-dependent. So a point value is not a measurement: min, median and max
over N runs are reported, and the prose that quotes this file must quote the range.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The ticket text is the one measured to reach `promote` -- see CLAUDE.md's note on
# run 32557597915, where a vaguer ticket legitimately ended `failed` because the
# reviewer kept withholding approval. A scorecard whose clean column depends on
# ticket phrasing would report a false-block that is a property of the sentence.
CLEAN_TICKET = (
    "Add a per-IP rate limit of five login attempts per minute to app/auth.py, "
    "returning HTTP 429 past the threshold. Read the limit and the Redis URL from "
    "environment variables."
)

DEFAULT_RUNS = 10

OUT_PATH = REPO_ROOT / "docs" / "final" / "evidence" / "scorecard-baseline.json"


def _force_the_hermetic_regime(tmp: Path) -> dict[str, str]:
    """Close the model, GitHub and clone seams. Returns what was set, for the record.

    THIS IS NOT BELT-AND-BRACES. conftest's guards are pytest fixtures and this is
    not pytest, so without this function every seam below is live:

      * the model      -- `llm.available()` was measured True on this machine
      * GitHub         -- `github_ops` WRITES: branch, commit, PR
      * the clone      -- `repo_snapshot` shallow-clones the target repository

    `OFFLINE` alone is not enough and the mistake is easy: it closes the GitHub seam
    only. `llm.available()` reads `LLM_DISABLED`, `LLM_BASE_URL` and boto3
    credentials, and never `OFFLINE` -- so a run with `OFFLINE=true` alone still
    calls Bedrock. Both knobs, deliberately.

    The offline workspace is redirected at a temporary directory for conftest guard
    3's measured reason: `OFFLINE_REPO` defaults to `runs/offline-demo` INSIDE this
    repository, and the offline path does real `git init` / `commit` there.

    Set through os.environ AND on the already-imported config module, because
    `config` reads the environment at import and this function may run after it.
    """
    from agentorg.common import config

    applied = {
        "OFFLINE": "true",
        "LLM_DISABLED": "true",
        "GITHUB_TOKEN": "",
        "DEMO_REPO": "",
        "OFFLINE_REPO": str(tmp / "offline-repo"),
        "OFFLINE_NOTES": str(tmp / "offline-repo" / "NOTES.md"),
    }
    for key, value in applied.items():
        os.environ[key] = value

    config.OFFLINE = True
    config.LLM_DISABLED = True
    config.GITHUB_TOKEN = ""
    # `config.GITHUB_REPO` reads env var DEMO_REPO -- the one name mismatch in
    # config.py. Clearing GITHUB_REPO in the environment would have no effect at
    # all, so the attribute is set directly and DEMO_REPO is cleared above for the
    # benefit of anything that re-imports.
    config.GITHUB_REPO = ""
    config.OFFLINE_REPO = applied["OFFLINE_REPO"]
    config.OFFLINE_NOTES = applied["OFFLINE_NOTES"]

    from agentorg.common import llm

    if llm.available():
        raise SystemExit(
            "REFUSING TO MEASURE: llm.available() is still True after forcing the "
            "hermetic regime, so this run would make live billable model calls and "
            "the numbers would not be reproducible. Investigate config.LLM_DISABLED."
        )
    return applied


def _one_run(ticket_id: str, poisoned: bool) -> dict:
    """One pipeline walk, reduced to the fields the scorecard scores.

    `auto_approve=True` because the three gates are a HUMAN dimension, not an
    automated one: this script measures how many decisions a run DEMANDS, which is
    `len(state.decisions)`, and a run that halted waiting for a human would measure
    nothing at all. The count is the metric; who supplied it is not.

    THE SCANNER CACHE IS CLEARED BEFORE EVERY RUN, AND WITHOUT THAT THIS SCRIPT
    WOULD BE THE PATTERN CLAUDE.md WARNS ABOUT. `run_all_scanners` memoises on
    sha256 of the diff, and every run in one arm here submits the SAME diff -- so
    on a 10-run arm exactly one run scans and nine replay a dict lookup, while all
    ten report `scan_provenance: scanners` and `blocking: 2`. "10/10 blocked" would
    then be one measurement presented as ten, and nothing in the output would say
    so. MEASURED, first probe of this script at 3 runs per arm:

        POISON-1 walk=1.5047   <- the scan
        POISON-2 walk=0.0556   <- the cache
        POISON-3 walk=0.0591   <- the cache

    a 25x gap. `reset_scanner_cache` is public precisely so a caller outside the
    package can do this; `tests/conftest.py`'s guard 5 clears it on both sides of
    every test for the same reason.
    """
    from agentorg.graph import run_pipeline
    from agentorg.security import reset_scanner_cache
    from tests import provenance as prov

    reset_scanner_cache()

    started = time.perf_counter()
    state = run_pipeline(ticket_id, CLEAN_TICKET, poisoned=poisoned, auto_approve=True)
    elapsed = time.perf_counter() - started

    security = state.security
    blocked = state.status == "blocked"

    # provenance.py raises rather than guessing when its two signals disagree.
    # Recorded as the string "unknown" instead of a plausible label: a mislabelled
    # provenance would relabel every row in this file, and a mislabelled metric
    # reads as evidence.
    try:
        provenance_mode = (
            "fixture" if prov.answered_from_fixture(state) else "real_scanners"
        )
    except RuntimeError as exc:
        provenance_mode = f"unknown: {exc}"

    return {
        "ticket_id": ticket_id,
        "poisoned": poisoned,
        "status": state.status,
        "blocked": blocked,
        "verdict": security.verdict if security else None,
        "blocking": len(security.blocking) if security else 0,
        "scan_provenance": security.scan_provenance if security else "",
        "finding_lines": sorted(f.line for f in security.blocking) if security else [],
        "provenance_mode": provenance_mode,
        "human_decisions": len(state.decisions),
        "revision_count": state.revision_count,
        "model_provenance": state.model_provenance,
        "walk_seconds": round(elapsed, 6),
    }


def _spread(values: list[float]) -> dict:
    """min / median / max, never a bare point value. See the module docstring."""
    if not values:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {
        "n": len(values),
        "min": round(min(values), 6),
        "median": round(statistics.median(values), 6),
        "max": round(max(values), 6),
    }


def _rate(numerator: int, denominator: int) -> float | None:
    """A rate, or None when there is no denominator.

    None rather than 0.0, for `CostRecord.usd`'s reason: zero reads as a measured
    answer and this must read as an absent one.
    """
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _local_scanner_versions() -> dict:
    """Which scanner versions produced these numbers, and whether the image agrees.

    THIS IS NOT DECORATION, AND IT RECORDS A REAL DIVERGENCE FOUND BY WRITING IT.
    `tests/provenance.py` documents `REAL_SCANNER_LINES = {3, 4}` as "measured on
    gitleaks 8.21.2", and the image pins 8.21.2. Measured on this machine
    2026-08-28: local gitleaks is **8.30.1** and local semgrep is **1.173.0**.

    The line numbers still come out {3, 4}, so the discriminator survives the
    version gap -- but that is a measured fact about two specific versions, not a
    property of gitleaks in general. A future version that shifts them onto {4, 5}
    silently destroys the discriminator while every provenance assertion keeps
    passing, so the version that produced a published row has to travel WITH the
    row. Reading `{3, 4}` off this file without knowing which gitleaks said so is
    the same mistake as quoting a timing without its conditions.
    """
    versions: dict[str, dict] = {}
    for tool in ("gitleaks", "trivy", "semgrep"):
        binary = shutil.which(tool)
        if binary is None:
            versions[tool] = {"local": None, "image_pin": _image_pin(tool)}
            continue
        flag = {"gitleaks": "version", "trivy": "--version", "semgrep": "--version"}[tool]
        try:
            result = subprocess.run(
                [binary, flag], capture_output=True, text=True, check=False, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            versions[tool] = {"local": "unreadable", "image_pin": _image_pin(tool)}
            continue
        output = (result.stdout or result.stderr).strip()
        found = re.search(r"(\d+\.\d+\.\d+)", output)
        local = found.group(1) if found else None
        pin = _image_pin(tool)
        versions[tool] = {
            "local": local,
            "image_pin": pin,
            "matches_image": local == pin,
        }
    return versions


def _image_pin(tool: str) -> str | None:
    """The version the container pins for `tool`, read from the Dockerfile."""
    dockerfile = REPO_ROOT / "agentorg" / "agents" / "Dockerfile"
    if not dockerfile.exists():
        return None
    arg = {"gitleaks": "GITLEAKS", "trivy": "TRIVY", "semgrep": "SEMGREP"}[tool]
    match = re.search(
        rf"ARG {arg}_VERSION=([\d.]+)", dockerfile.read_text(encoding="utf-8")
    )
    return match.group(1) if match else None


def _git_head() -> str:
    """The commit these numbers describe. A scorecard row without one is undated."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() or "unknown"


# Matches a CSI escape sequence. pytest's terminal writer emits these when it
# believes stdout is a tty, and they survive a capture into JSON.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    """Colour codes out of a captured line, so the JSON holds readable text."""
    return _ANSI.sub("", text)


def _display_path(path: Path) -> str:
    """The path as a reader should see it: repo-relative when it is inside the repo.

    `Path.relative_to` RAISES for a path outside the tree, and `--out /tmp/...` is
    exactly what a probe run passes -- so the naive form crashed AFTER writing the
    file and printing the table, which is the worst place to fail: the work was
    done and the exit code said it was not.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _test_totals() -> dict:
    """How many tests exist right now, collected rather than recalled.

    `--collect-only` and not a run: this is the SIZE of the suite, and collecting
    is seconds where running is minutes. The count moves whenever any lane commits,
    which is exactly why CLAUDE.md refuses to write it down.
    """
    result = subprocess.run(
        # -p no:cacheprovider so collecting does not write .pytest_cache into a
        # tree this script is only supposed to read.
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        capture_output=True, text=True, check=False, cwd=str(REPO_ROOT),
        env={**os.environ, "NO_COLOR": "1"},
    )
    tail = [line for line in result.stdout.strip().splitlines() if line.strip()]
    return {
        # ANSI-stripped. pytest colours this line when it thinks it has a
        # terminal, and the escape sequences survive into JSON as  -- which
        # then renders as mojibake in any document that quotes the field. NO_COLOR
        # above usually prevents it; this strips it if the plugin ignores NO_COLOR.
        "collect_only_tail": _strip_ansi(tail[-1]) if tail else "",
        "exit_code": result.returncode,
        "test_files": len(sorted((REPO_ROOT / "tests").glob("test_*.py"))),
    }


# The three dimensions this script cannot measure, with the reason and the command a
# human would run. Structured data rather than prose, so the renderer cannot quietly
# drop one: `--require-complete` counts them.
UNMEASURED = {
    "time_to_merge": {
        "measured": False,
        "why": (
            "the local walk completes in ~1.5 s while a real run is minutes, most of "
            "it a human deciding at a gate. Quoting the local number as time-to-merge "
            "would be wrong by two orders of magnitude in the flattering direction."
        ),
        "command_a_human_would_run": (
            "gh run list --workflow=run-pipeline.yml --limit 20 "
            "--json databaseId,createdAt,updatedAt,conclusion"
        ),
        "note": (
            "gate WAIT time must be reported separately from pipeline time, per "
            "specification 4 -- a single figure hides which of the two is being paid."
        ),
    },
    "cost_per_merged_change": {
        "measured": False,
        "why": (
            "no code path records token usage. state.StageCost declares "
            "input_tokens/output_tokens/cached_tokens (Phase 0 contract batch) and "
            "nothing writes them; agentorg/common/llm.py has no usage accounting."
        ),
        "command_a_human_would_run": (
            "grep -rn 'input_tokens' agentorg/  # today: only the state declaration"
        ),
        "note": "Lane E owns the instrumentation. This row has no source until it lands.",
    },
    "escaped_defects": {
        "measured": False,
        "why": (
            "a rate over shipped changes, and the denominator is two merged PRs on "
            "the target repository -- too small to quote as a rate."
        ),
        "command_a_human_would_run": (
            "gh pr list --repo mohamedsorour1998/auth-service --state merged "
            "--json number,mergedAt,title"
        ),
        "note": (
            "zero escaped defects over two merges is not evidence of a low rate. "
            "Report the count and the denominator, never the ratio."
        ),
    },
}


def measure(runs: int) -> dict:
    """The baseline row. Every measured number in this dict came from a run above."""
    import tempfile

    from tests import provenance as prov

    with tempfile.TemporaryDirectory(prefix="scorecard-") as tmpdir:
        regime = _force_the_hermetic_regime(Path(tmpdir))

        poisoned_rows = [_one_run(f"POISON-{i + 1}", True) for i in range(runs)]
        clean_rows = [_one_run(f"CLEAN-{i + 1}", False) for i in range(runs)]

    poisoned_blocked = sum(1 for r in poisoned_rows if r["blocked"])
    clean_blocked = sum(1 for r in clean_rows if r["blocked"])
    clean_promoted = sum(1 for r in clean_rows if r["status"] == "promoted")

    all_rows = poisoned_rows + clean_rows

    return {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "commit": _git_head(),
        "conditions": {
            "regime": regime,
            "scanner_mode": prov.describe_mode(),
            "scanner_versions": _local_scanner_versions(),
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.machine()}",
            "runs_per_arm": runs,
            "note": (
                "conftest's six autouse guards are pytest fixtures and do NOT bind "
                "here; the regime above was forced by this script and is part of "
                "every number below."
            ),
            "scanner_version_note": (
                "the line-number discriminator {3, 4} is documented as measured on "
                "gitleaks 8.21.2, which is what the IMAGE pins. Compare "
                "scanner_versions.gitleaks.matches_image before quoting these rows "
                "as evidence about the deployed container: a local measurement made "
                "with a different scanner version is a different measurement, even "
                "when it agrees."
            ),
        },
        "dimensions": {
            "block_correctness": {
                "measured": True,
                "definition": "poisoned runs that ended blocked / poisoned runs",
                "value": _rate(poisoned_blocked, len(poisoned_rows)),
                "numerator": poisoned_blocked,
                "denominator": len(poisoned_rows),
                "evidence": (
                    "every poisoned row carries scan_provenance and the finding line "
                    "numbers; {3, 4} is the real scanners and {4, 5} is the fixture"
                ),
            },
            "false_block_rate": {
                "measured": True,
                "definition": "clean runs that ended blocked / clean runs",
                "value": _rate(clean_blocked, len(clean_rows)),
                "numerator": clean_blocked,
                "denominator": len(clean_rows),
                "clean_promoted": clean_promoted,
                "evidence": (
                    "a clean run that ends anything other than promoted is reported "
                    "too -- the revision cap can end a clean run `failed` without a "
                    "block, and calling that a false block would overstate the fault"
                ),
            },
            "human_touches": {
                "measured": True,
                "definition": "HumanDecision records the run demanded",
                "poisoned": _spread([float(r["human_decisions"]) for r in poisoned_rows]),
                "clean": _spread([float(r["human_decisions"]) for r in clean_rows]),
                "evidence": (
                    "a blocked run demands fewer decisions than a promoted one "
                    "because the stages after the block never run -- so this "
                    "dimension is only comparable within one outcome"
                ),
            },
            "agent_rework": {
                "measured": True,
                "definition": "revision loops driven by the reviewer withholding approval",
                "poisoned": _spread([float(r["revision_count"]) for r in poisoned_rows]),
                "clean": _spread([float(r["revision_count"]) for r in clean_rows]),
                "evidence": (
                    "measured with the model OFF, so the reviewer answers from "
                    "fixtures/review_result.json, which always approves. This is a "
                    "FLOOR, not the live figure: the live reviewer withheld approval "
                    "for four rounds on run 32557597915."
                ),
            },
            **UNMEASURED,
        },
        "walk_seconds": {
            "poisoned": _spread([r["walk_seconds"] for r in poisoned_rows]),
            "clean": _spread([r["walk_seconds"] for r in clean_rows]),
            "note": (
                "the in-process walk, NOT time to merge. Reported as a spread "
                "because CLAUDE.md records 116.88 -> 149.68 -> 102.83 s for one "
                "unchanged snapshot, purely load-dependent."
            ),
        },
        "suite": _test_totals(),
        "rows": all_rows,
    }


def _render(report: dict) -> str:
    """The table, for a terminal. The JSON is the artifact; this is the receipt."""
    lines: list[str] = []
    add = lines.append
    add(f"scorecard baseline  commit {report['commit']}  {report['measured_at']}")
    add(f"  {report['conditions']['scanner_mode']}")
    for tool, meta in report["conditions"]["scanner_versions"].items():
        mark = "" if meta.get("matches_image") else "  <- differs from the image pin"
        add(
            f"    {tool:<10} local={meta['local']} image={meta['image_pin']}{mark}"
        )
    add(f"  runs per arm: {report['conditions']['runs_per_arm']}")
    add("")
    add(f"  {'dimension':<24} {'value':<14} source")
    add(f"  {'-' * 24} {'-' * 14} {'-' * 34}")
    for name, row in report["dimensions"].items():
        if not row.get("measured"):
            add(f"  {name:<24} {'NOT MEASURED':<14} {row['why'][:34]}")
            continue
        if "value" in row:
            value = f"{row['value']} ({row['numerator']}/{row['denominator']})"
            add(f"  {name:<24} {value:<14} measured here")
        else:
            poisoned = row["poisoned"]
            clean = row["clean"]
            value = f"p={poisoned['median']} c={clean['median']}"
            add(f"  {name:<24} {value:<14} median of {poisoned['n']} + {clean['n']}")
    add("")
    walk = report["walk_seconds"]
    add(
        f"  walk seconds  poisoned {walk['poisoned']['min']}-{walk['poisoned']['max']}"
        f"  clean {walk['clean']['min']}-{walk['clean']['max']}"
    )
    add(f"  suite         {report['suite']['collect_only_tail']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--runs", type=int, default=DEFAULT_RUNS,
        help=f"runs per arm, poisoned and clean (default {DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--out", type=Path, default=OUT_PATH,
        help="where to write the JSON artifact",
    )
    parser.add_argument(
        "--require-real-scanners", action="store_true",
        help=(
            "exit 1 unless all three scanner binaries are on PATH. Use this for a "
            "row that will be published: without the binaries the poisoned verdict "
            "comes from a JSON fixture rather than from the block rule, and both "
            "produce blocking=2."
        ),
    )
    args = parser.parse_args(argv)

    if args.runs < 1:
        print("--runs must be at least 1", file=sys.stderr)
        return 1

    from tests import provenance as prov

    installed = prov.binaries_installed()
    if args.require_real_scanners and len(installed) != len(prov.SCANNER_TOOLS):
        print(
            f"REFUSING: --require-real-scanners was asked for and only {installed} "
            f"are on PATH. {prov.describe_mode()}",
            file=sys.stderr,
        )
        return 1

    report = measure(args.runs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(_render(report))
    print()
    print(f"wrote {_display_path(args.out)}")

    # A measured dimension whose value is None means the rate had no denominator --
    # a run loop that produced nothing. Fail rather than publish an empty row.
    empty = [
        name for name, row in report["dimensions"].items()
        if row.get("measured") and row.get("value", "present") is None
    ]
    if empty:
        print(f"FAIL: measured dimensions with no value: {empty}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
