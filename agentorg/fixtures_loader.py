"""Load validated fixtures so every stub returns real, schema-correct data.

This is the glue that lets all five lanes work in parallel on day 1: instead of
waiting for a teammate's real code, your stub loads their sample result from
fixtures/ (produced and validated by make_fixtures.py).

Nobody edits this file — it just reads JSON.
"""

import json
import pathlib

from .state import (
    PlanResult,
    DevResult,
    ReviewResult,
    SecurityResult,
    SREResult,
)

_FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def plan() -> PlanResult:
    return PlanResult.model_validate(_load("plan_result.json"))


def dev(poisoned: bool = False) -> DevResult:
    name = "dev_result_poisoned.json" if poisoned else "dev_result_clean.json"
    return DevResult.model_validate(_load(name))


def review() -> ReviewResult:
    return ReviewResult.model_validate(_load("review_result.json"))


def security(block: bool = False) -> SecurityResult:
    name = "security_result_block.json" if block else "security_result_pass.json"
    return SecurityResult.model_validate(_load(name))


def sre() -> SREResult:
    return SREResult.model_validate(_load("sre_result.json"))
