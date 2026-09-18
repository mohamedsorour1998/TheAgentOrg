"""EVERY COST ROW IN EVERY RUN SAID `plan`, AND THE RUN SCREEN SHOWED IT.

Reported from the deployed app, reading a run's cost table:

    STAGE   MODEL                     INPUT    OUTPUT
    plan    us.amazon.nova-2-lite-v1:0  5,739      288
    plan    us.amazon.nova-2-lite-v1:0 24,326    1,671
    plan    us.amazon.nova-2-lite-v1:0  6,658      182

Three calls, made by three different agents, all labelled `plan`. `_emit` called
`cost_record.build_cost_record()` with NO argument, so `llm.attribute_usage_to` was
never called, so every usage row reached `_stage_or_fallback` with a blank stage and
took the `plan` fallback.

**IT ANSWERED "WHICH STAGE IS EXPENSIVE" WITH A WRONG WORD RATHER THAN WITH A GAP**,
which is this repository's signature defect in a display: an absent measurement
rendered as a measured value. The fallback itself is correct and documented -- it
exists because `StageCost.stage` is a `Stage` Literal and pydantic refuses `""` --
but nothing was supplying the label it exists to stand in for.

CLAUDE.md records this as an open item: *"Per-stage attribution needs one line per
stage in `graph.py` / `run_stage.py` -- the integrator's files."* `_cost_stage` is
that line for the cloud path.
"""

from __future__ import annotations

import importlib.util
import pathlib
import typing

import pytest

from agentorg.common import llm
from agentorg.cost import record as cost_record
from agentorg.state import Stage

STAGE_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "run_stage.py"


def _stage_module():
    """Import scripts/run_stage.py without making scripts/ a package.

    The same helper `test_promote_guard.py` and `test_failed_run_rendering.py` use.
    NOT `pytest.importorskip("run_stage")`: `scripts/` is not on `sys.path`, so that
    spelling skipped this entire file and reported `1 skipped` -- a green run that
    tested nothing, which is the exact shape this file exists to catch one layer up.
    """
    spec = importlib.util.spec_from_file_location("run_stage_cost_test", STAGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


run_stage = _stage_module()


@pytest.fixture(autouse=True)
def _clean_usage():
    """The recorder is MODULE STATE, so a leaked row is another test's cost."""
    llm.reset_usage()
    yield
    llm.reset_usage()


def _record_one_call(input_tokens: int = 10, output_tokens: int = 2) -> None:
    """One model call, as `llm` records it. No network, no model."""
    llm._record_usage(
        llm.Usage(
            model="us.amazon.nova-2-lite-v1:0",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=0,
            cached_reported=False,
        )
    )


class TestWhichStageAModelCallIsAttributedTo:
    def test_a_stage_name_reaches_the_cost_row(self):
        """THE FIX, and the whole of it: a row carries the stage that made the call."""
        _record_one_call()
        record = cost_record.build_cost_record("security")

        assert [row.stage for row in record.stages] == ["security"], (
            "the call was attributed to "
            f"{[row.stage for row in record.stages]}, not to the stage that made it"
        )

    def test_with_no_stage_it_falls_back_to_plan_which_is_what_went_wrong(self):
        """THE OLD BEHAVIOUR, pinned so the fix is visibly a change and not a no-op.

        This is not a defect in `build_cost_record` -- the fallback is deliberate and
        `_stage_or_fallback` explains it. It is what every run got, because nobody
        passed the argument.
        """
        _record_one_call()
        record = cost_record.build_cost_record()

        assert [row.stage for row in record.stages] == ["plan"]

    def test_run_stage_supplies_the_stage_it_was_invoked_with(self, monkeypatch):
        """`_cost_stage` reads the ONE place that knows which stage this process is.

        On the cloud path each stage is a separate process, so the argument the
        process was invoked with is exactly the stage its model calls served.
        `_emit` has eleven call sites and its own comment warns that a per-call-site
        argument would be eleven chances to omit one -- so it is recorded once by
        `main` and read here.
        """
        monkeypatch.setattr(run_stage, "_THIS_STAGE", "develop")
        assert run_stage._cost_stage() == "develop"

    @pytest.mark.parametrize(
        "recorder", ["gate1-rejected", "gate2-rejected", "gate3-rejected"]
    )
    def test_a_rejection_recorder_attributes_nothing_rather_than_raising(
        self, monkeypatch, recorder
    ):
        """**THE GUARD, AND WITHOUT IT THE COST PATH COULD FAIL A RUN.**

        `STAGES` carries three recorder names that are NOT `Stage` members.
        `StageCost.stage` is a `Stage` Literal, so attributing one makes pydantic
        raise -- inside the module `cost/record.py` says "must never be the thing
        that fails a run". A recorder fires on a REFUSED run, so the failure would
        land on exactly the runs whose record matters most.
        """
        assert recorder not in typing.get_args(Stage), (
            f"{recorder} is a Stage member now; this test would pin nothing"
        )
        monkeypatch.setattr(run_stage, "_THIS_STAGE", recorder)
        assert run_stage._cost_stage() == ""

        # AND IT IS SAFE ALL THE WAY THROUGH, not merely filtered. Asserting the
        # filter alone would pass against a `build_cost_record` that raised anyway.
        _record_one_call()
        record = cost_record.build_cost_record(run_stage._cost_stage())
        assert [row.stage for row in record.stages] == ["plan"]

    def test_every_advancing_stage_name_is_a_Stage_member(self):
        """ANTI-VACUITY for the filter: it must admit the nine and refuse the three.

        A `_cost_stage` that answered `""` for everything would pass the recorder
        test above and silently restore the defect. This is the other direction.
        """
        members = set(typing.get_args(Stage))
        advancing = [s for s in run_stage.STAGES if not s.endswith("-rejected")]
        assert advancing, "STAGES is empty; this test would pin nothing"
        assert set(advancing) <= members, (
            f"{sorted(set(advancing) - members)} are dispatchable stages that "
            f"`_cost_stage` would discard, so their model calls stay unattributed"
        )
