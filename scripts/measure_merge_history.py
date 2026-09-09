"""Measure `time_to_merge` and `escaped_defects` from the REAL GitHub history.

The scorecard listed both as honest gaps needing "real runs over real time, not a
harness". The runs exist: `mohamedsorour1998/auth-service` carries eight
pipeline-produced merges and `run-pipeline.yml` carries thirty-seven runs. So the
gap was never "no data" -- it was "nobody asked GitHub". This script asks.

It REFUSES rather than reporting a number it could not measure, which is
`measure_scorecard.py`'s rule and the reason `--require-real-scanners` exists there.
`gh` absent, `gh` unauthenticated, zero merged pull requests, or a credential scan
that cannot find a credential anywhere all exit non-zero.

THE POSITIVE CONTROL IS THE PART THAT MAKES THE ZERO MEAN ANYTHING. `escaped_defects`
comes out 0, and a broken grep produces 0 just as readily as a clean history. So the
scan is also run against the pull requests that did NOT merge, and the script
REFUSES if the credential is not found there -- PR #44 is the poisoned run, blocked
by `compute_security_verdict`, and its diff carries `AKIAIOSFODNN7EXAMPLE` on an
added line. A scan that finds nothing in the merged set AND nothing in the blocked
set has measured nothing at all.

    python scripts/measure_merge_history.py                 # read the committed artifact
    python scripts/measure_merge_history.py --refresh       # RE-MEASURE from live GitHub
    python scripts/measure_merge_history.py --json          # the raw rows
    python scripts/measure_merge_history.py --out rows.json # also write them somewhere

**The default reads `docs/final/evidence/merge-history.json` and touches no
network.** `--refresh` is the only mode that calls GitHub, and it rewrites that
artifact with a `measured_at` stamp. See the note on `ARTIFACT` below for why: an
existing guard runs every `measure_*.py` on every suite run, and an unconditional
network call here would have put live GitHub inside a suite whose defining property
is that it needs none.

TWO THINGS THE NUMBERS DO NOT SAY, both printed beside them rather than left to a
reader. `time_to_merge` is measured over MERGES, so every blocked and failed run is
excluded -- survivorship, and the merge rate is printed as the denominator. And the
gates were clicked by a human who was sitting there waiting; the figure is a floor
on machine time, not an estimate of what a reviewer with a day job costs.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

# THE DEFAULT READS A COMMITTED ARTIFACT, AND `--refresh` IS THE NETWORK.
#
# Caught by `tests/test_evidence.py::test_every_measure_script_runs_and_exits_zero`,
# which runs every `scripts/measure_*.py` on every suite run. The first version of
# this script measured from GitHub unconditionally, so that guard would have put
# ~20 live GitHub API calls inside `pytest -q` -- breaking the property the whole
# suite is built on ("deliberately hermetic, no AWS, no GitHub, no scanners") and
# turning the suite red on any machine without an authenticated `gh`.
#
# Same shape as `scorecard-baseline.json`: the artifact carries the timestamp it was
# measured at, and a number nobody has refreshed goes stale VISIBLY rather than
# silently. Re-measure with `--refresh`.
ARTIFACT = Path(__file__).resolve().parent.parent / "docs/final/evidence/merge-history.json"

TARGET_REPO = "mohamedsorour1998/auth-service"
PIPELINE_REPO = "mohamedsorour1998/TheAgentOrg"
WORKFLOW = "run-pipeline.yml"

# The poison, as `tickets/poisoned.md` carries it. AWS's own published example key,
# so it authenticates nothing -- CLAUDE.md's Secrets section. Both spellings are
# scanned because the demo greps the VARIABLE NAME rather than the key: the key
# appears twice in that ticket (prose and code) and "which one is real?" is not a
# question to answer mid-demo.
CREDENTIAL_PATTERNS = ("AKIAIOSFODNN7EXAMPLE", "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID")

# A PR whose title starts "<n>: " was opened by the pipeline for issue <n>. The
# hand-written ci.yml PR (#18) has no such prefix and is excluded from
# time_to_merge for that reason -- it is not a pipeline-produced change and
# including it would put a human's 72-minute review inside a machine's median.
_TICKET_PREFIX = re.compile(r"^(\d+):")


class CannotMeasure(RuntimeError):
    """Raised when the answer would have to be invented."""


def _gh(*args: str) -> str:
    if not shutil.which("gh"):
        raise CannotMeasure("the `gh` CLI is not on PATH; nothing here can reach GitHub")
    proc = subprocess.run(("gh", *args), capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0:
        raise CannotMeasure(f"gh {' '.join(args)} exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def _pull_diff(number: int) -> str:
    return _gh(
        "api", f"repos/{TARGET_REPO}/pulls/{number}",
        "-H", "Accept: application/vnd.github.v3.diff",
    )


def _credential_hits(diff: str) -> int:
    """Matches on ADDED lines only, via the same rule `common/diff.py` applies.

    A key on a `-` line is a key being REMOVED, and counting it would report the
    remediation as the defect. This is `developer._key_is_in_the_change`'s measured
    lesson: the whole-diff form let a poisoned ticket promote, because every
    revision after the reviewer's objection carries the key on a deletion.
    """
    added = [ln for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    return sum(1 for ln in added for pat in CREDENTIAL_PATTERNS if pat in ln)


def measure() -> dict:
    merged = json.loads(_gh(
        "pr", "list", "--repo", TARGET_REPO, "--state", "merged", "--limit", "100",
        "--json", "number,title,createdAt,mergedAt",
    ))
    if not merged:
        raise CannotMeasure(f"{TARGET_REPO} reports zero merged pull requests")

    issues = {
        i["number"]: i
        for i in json.loads(_gh(
            "issue", "list", "--repo", TARGET_REPO, "--state", "all", "--limit", "200",
            "--json", "number,createdAt,title",
        ))
    }

    rows, skipped = [], []
    for pr in sorted(merged, key=lambda p: p["number"]):
        m = _TICKET_PREFIX.match(pr["title"])
        if not m or int(m.group(1)) not in issues:
            skipped.append(pr["number"])
            continue
        issue = issues[int(m.group(1))]
        rows.append({
            "issue": issue["number"],
            "pr": pr["number"],
            "ticket_to_merge_min": (_at(pr["mergedAt"]) - _at(issue["createdAt"])).total_seconds() / 60,
            "pr_open_to_merge_min": (_at(pr["mergedAt"]) - _at(pr["createdAt"])).total_seconds() / 60,
        })
    if not rows:
        raise CannotMeasure("no merged pull request could be matched to an issue")

    # ESCAPED DEFECTS, over every merged PR including the hand-written one: the
    # question is what reached `main`, and a human's change reaching main is as much
    # a shipped change as an agent's.
    escapes = {p["number"]: _credential_hits(_pull_diff(p["number"])) for p in merged}

    # THE POSITIVE CONTROL. Without it a zero above is unfalsifiable.
    controls = json.loads(_gh(
        "pr", "list", "--repo", TARGET_REPO, "--state", "all", "--limit", "100",
        "--json", "number,state",
    ))
    unmerged = [p["number"] for p in controls if p["state"] != "MERGED"]
    control_hits = {}
    for number in sorted(unmerged, reverse=True):
        hits = _credential_hits(_pull_diff(number))
        if hits:
            control_hits[number] = hits
            break
    if not control_hits:
        raise CannotMeasure(
            "the credential scan found nothing in ANY unmerged pull request, so its "
            "zero over the merged set is not evidence -- a broken scan reads the same"
        )

    runs = json.loads(_gh(
        "run", "list", "--repo", PIPELINE_REPO, "--workflow", WORKFLOW,
        "--limit", "200", "--json", "conclusion",
    ))
    outcomes: dict[str, int] = {}
    for r in runs:
        outcomes[r["conclusion"] or "in_progress"] = outcomes.get(r["conclusion"] or "in_progress", 0) + 1

    return {
        "merges": rows,
        "skipped_prs": skipped,
        "escaped_by_pr": escapes,
        "positive_control": control_hits,
        "pipeline_runs": len(runs),
        "pipeline_outcomes": outcomes,
    }


def render(data: dict) -> str:
    rows = data["merges"]
    tm = [r["ticket_to_merge_min"] for r in rows]
    pm = [r["pr_open_to_merge_min"] for r in rows]
    escaped = sum(data["escaped_by_pr"].values())
    when = data.get("measured_at", "unknown")
    out = [f"MEASURED FROM LIVE GITHUB HISTORY at {when}", ""]
    for r in rows:
        out.append(
            f"  issue #{r['issue']:>3} -> PR #{r['pr']:>3}   "
            f"ticket->merge {r['ticket_to_merge_min']:8.2f} min   "
            f"pr->merge {r['pr_open_to_merge_min']:5.2f} min"
        )
    out += [
        "",
        f"TIME TO MERGE   n={len(rows)} pipeline-produced merges",
        f"  ticket opened -> merged   min {min(tm):.2f}  median {median(tm):.2f}  max {max(tm):.2f}  minutes",
        f"  pr opened     -> merged   min {min(pm):.2f}  median {median(pm):.2f}  max {max(pm):.2f}  minutes",
        "",
        (
            f"  SURVIVORSHIP: {len(rows)} merges out of {data['pipeline_runs']} pipeline runs "
            f"({', '.join(f'{k}={v}' for k, v in sorted(data['pipeline_outcomes'].items()))})."
        ),
        "  Every blocked and failed run is EXCLUDED from the median above.",
        "  The gates were clicked by somebody watching. This is a floor on machine time.",
        "",
        (
            f"ESCAPED DEFECTS  {escaped} credential escapes over "
            f"{len(data['escaped_by_pr'])} merged pull requests"
        ),
        (
            f"  positive control: PR #{next(iter(data['positive_control']))} (not merged) carries "
            f"{next(iter(data['positive_control'].values()))} credential match(es) on added lines"
        ),
        "  A COUNT AND A DENOMINATOR, NEVER A RATE. Zero over nine merges is not a low",
        "  rate; and the scanners bind on credentials, CVEs and injectable patterns only,",
        "  so this is silent about a logic defect that shipped.",
    ]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the raw rows")
    parser.add_argument(
        "--refresh", action="store_true",
        help="re-measure from LIVE GitHub and rewrite the artifact (needs `gh`, authenticated)",
    )
    parser.add_argument("--out", type=Path, help="also write the rows to this path")
    args = parser.parse_args()

    if args.refresh:
        try:
            data = measure()
        except CannotMeasure as exc:
            print(f"CANNOT MEASURE: {exc}", file=sys.stderr)
            return 1
        # `commit`, `measured_at` and a non-empty `conditions` are required of every
        # published artifact by `test_every_published_json_artifact_records_its_conditions`,
        # and the requirement is right: a number without its conditions is not a
        # measurement. The conditions that matter here are WHICH repositories were
        # read and WHAT counted as a credential -- change either and the figures move
        # while the field names stay identical.
        data["measured_at"] = datetime.now(UTC).isoformat()
        data["commit"] = subprocess.run(
            ("git", "rev-parse", "HEAD"), capture_output=True, text=True,
            cwd=ARTIFACT.parent, check=False,
        ).stdout.strip() or "unknown"
        data["conditions"] = {
            "target_repo": TARGET_REPO,
            "pipeline_repo": PIPELINE_REPO,
            "workflow": WORKFLOW,
            "credential_patterns": list(CREDENTIAL_PATTERNS),
            "scanned": "ADDED lines only, per common/diff.py",
            "time_to_merge_excludes": "every run that did not merge (survivorship)",
            "gates": "clicked by a human who was watching; a floor on machine time",
        }
        ARTIFACT.write_text(json.dumps(data, indent=2) + "\n")
    else:
        if not ARTIFACT.exists():
            print(
                f"CANNOT MEASURE: {ARTIFACT} is absent and --refresh was not given. "
                f"This command reads a committed artifact by default; re-measure with "
                f"--refresh.",
                file=sys.stderr,
            )
            return 1
        data = json.loads(ARTIFACT.read_text())

    if args.out:
        args.out.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data, indent=2) if args.json else render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
