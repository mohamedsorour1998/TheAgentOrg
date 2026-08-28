"""Assemble the parity table from measured runs. Lane F, F2 and F6.

    PYTHONPATH=. .venv-main/bin/python scripts/selfhost_parity.py \
        --baseline /tmp/lane-f-runs/bedrock-*.json \
        --local    /tmp/lane-f-runs/ollama-*.json

READS JSON, MEASURES NOTHING. `selfhost_measure.py` runs the pipeline; this script
only folds its records into a table. The split is deliberate: a script that both
measured and concluded could report a comparison no run supports, and the two
sides must be measurable on different days and different machines -- a laptop with
a GPU is not the machine that holds the AWS credentials.

It refuses rather than rendering when a side has no runs, because a table with an
empty column reads as unfinished rather than as unknown, and a reader fills a gap
with an assumption. `--allow-unmeasured` renders it anyway with the gap stated,
for the case where reporting "we could not measure this" IS the deliverable.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from agentorg.selfhost import ParitySet, RunObservation, render_parity_table


def _load(paths: list[str]) -> tuple[str, list[RunObservation], list[dict]]:
    """Every record at `paths` as observations, plus the raw records.

    The LABEL comes from the records rather than from a flag, so the table cannot
    name a side something other than what was measured. A mismatch across records
    is joined with `+` rather than silently taking the first -- two different
    models folded into one column would be the least detectable error this script
    could make.
    """
    observations: list[RunObservation] = []
    records: list[dict] = []
    labels: list[str] = []
    for path in paths:
        record = json.loads(pathlib.Path(path).read_text())
        records.append(record)
        raw = dict(record["observation"])
        observations.append(RunObservation(**raw))
        labels.append(record.get("label", "") or raw.get("label", ""))
    unique = sorted(set(labels))
    return "+".join(unique) if unique else "(unlabelled)", observations, records


def _network_lines(name: str, records: list[dict]) -> list[str]:
    """What the witness recorded for one side, per run.

    Printed PER RUN rather than folded, because the air-gap claim is about
    individual runs: one run out of three reaching AWS is the finding, and any
    fold that reported "mostly air-gapped" would bury it.
    """
    lines = [f"{name}:"]
    if not records:
        lines.append("  (no runs)")
        return lines
    for record in records:
        summary = record.get("network_summary", "(no witness record)")
        lines.append(f"  {summary}")
        unattributable = [h for h in record.get("hosts", [])
                          if h not in record.get("aws_hosts", [])]
        if record.get("aws_hosts"):
            lines.append(f"    AWS hosts: {', '.join(record['aws_hosts'])}")
        if unattributable:
            shown = ", ".join(unattributable[:4])
            more = "" if len(unattributable) <= 4 else f" (+{len(unattributable) - 4} more)"
            lines.append(f"    other hosts: {shown}{more}")
    # ONE scope note for the side, not one per run: it is a property of the
    # mechanism rather than of a run, and repeating it trains a reader to skip it.
    lines.append(f"  {records[0].get('network_scope', '')}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", nargs="*", default=[],
                        help="JSON records for the baseline side (Bedrock)")
    parser.add_argument("--local", nargs="*", default=[],
                        help="JSON records for the self-hosted side")
    parser.add_argument("--allow-unmeasured", action="store_true",
                        help="render even when a side has no runs, stating the gap")
    args = parser.parse_args(argv)

    baseline_label, baseline_runs, baseline_records = _load(args.baseline)
    local_label, local_runs, local_records = _load(args.local)

    if not args.allow_unmeasured and not (baseline_runs and local_runs):
        missing = "baseline" if not baseline_runs else "local"
        print(f"REFUSING: the {missing} side has no runs, so every row would be a "
              f"difference against nothing. Pass --allow-unmeasured to render it "
              f"with the gap stated.", file=sys.stderr)
        return 2

    baseline = ParitySet(baseline_label or "baseline", baseline_runs)
    local = ParitySet(local_label or "self-hosted", local_runs)

    print("PARITY -- what changes when the model runs on our own compute")
    print()
    for line in render_parity_table(baseline, local):
        print(line)
    print()
    print("NETWORK EVIDENCE")
    for line in _network_lines(baseline.label, baseline_records):
        print(line)
    for line in _network_lines(local.label, local_records):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
