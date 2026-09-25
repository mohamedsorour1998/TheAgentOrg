"""The merge-history report must not print a count it did not measure.

Found 2026-09-25 by re-running `scripts/measure_merge_history.py --refresh`: the
headline read `0 credential escapes over 13 merged pull requests` and the caveat two
lines below it read `Zero over nine merges is not a low rate` -- a literal written when
the denominator was nine, still printed beside the measured thirteen. A report whose
own caveat contradicts its headline is this repository's "number in prose" failure,
arriving in a script's output.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "measure_merge_history.py"
ARTIFACT = REPO_ROOT / "docs" / "final" / "evidence" / "merge-history.json"


def _render(data: dict) -> str:
    spec = importlib.util.spec_from_file_location("measure_merge_history", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.render(data)


def test_the_caveat_names_the_same_denominator_as_the_headline():
    data = json.loads(ARTIFACT.read_text())
    merged = len(data["escaped_by_pr"])
    # ANTI-VACUITY: the stale literal was "nine", so an artifact with exactly nine
    # merged pull requests could not tell the literal from the measurement.
    assert merged != 9, "the artifact has nine merged PRs; this test cannot see the defect"

    text = _render(data)
    headline = re.search(r"credential escapes over (\d+) merged pull requests", text)
    assert headline, "the headline line was not found; this test would pin nothing"
    assert int(headline.group(1)) == merged

    caveat = re.search(r"NEVER A RATE\. (\d+) over (\d+) merges", text)
    assert caveat, f"the caveat does not state its count and denominator:\n{text}"
    assert int(caveat.group(2)) == merged
    assert int(caveat.group(1)) == sum(data["escaped_by_pr"].values())
