"""The tenancy index is CONFIGURED somewhere, not merely implemented.

`run_index.record_run` returns False for "no table configured" and never raises --
a deliberate design, argued in its own docstring: an index is not the run's record,
so a failure to index must not fail a run that has already done its work.

**THAT DESIGN IS RIGHT, AND IT IS EXACTLY WHY THE GAP SURVIVED FOR A WEEK.** Both
the writer and the three readers gated on `TENANT_DB`, a Postgres DSN that the
operator's DynamoDB-only decision guarantees is never set. So every run indexed
nothing, every reader reported `indexed: false`, and every gate stayed green.
Measured 2026-09-15 against the live table:

    aws dynamodb scan --table-name theagentorg-tenancy   ->  "count": 0

Zero rows after every run this project had ever done.

`tests/test_reader_scoping.py` pins that the four gates NAME the same variable.
This file pins the other half: that something actually SETS it. A correct gate
nobody configures is the same empty screen as a wrong one, and neither raises.

**WHY NOT ASSERT AGAINST THE LIVE ACCOUNT.** This suite is hermetic by
construction, and an assertion about the deployed Amplify environment would need an
AWS call. So these read the two COMMITTED files that configure the two places a run
row is written and read, and `scripts/preflight.py` remains where a live claim
belongs.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE = REPO_ROOT / ".github" / "workflows" / "run-pipeline.yml"
BUILDSPEC = REPO_ROOT / "amplify.yml"

GATE = "TENANCY_TABLE"


def test_the_pipeline_sets_the_index_gate():
    """The WRITER's half. Four jobs index or revise a run; none of them can say so.

    `yaml.safe_load` is used for the workflow's `env:` block and NOT for its
    triggers -- YAML 1.1 coerces `on` to the boolean `True`, so `workflow["on"]`
    raises `KeyError` while `workflow[True]` is the trigger block. That trap is
    recorded in CLAUDE.md and is avoided here by only reading `env`.
    """
    workflow = yaml.safe_load(PIPELINE.read_text())
    env = workflow.get("env") or {}

    assert env, f"{PIPELINE.name} declares no workflow-level env; this test pins nothing"
    assert GATE in env, (
        f"{PIPELINE.name} does not set {GATE}. `run_index.record_run` then returns "
        f"False for every run and NEVER RAISES, so the pipeline stays green while "
        f"the web app's run list stays permanently empty."
    )
    assert env[GATE].strip(), (
        f"{PIPELINE.name} sets {GATE} to a blank value, which the gate reads as "
        f"'not configured' -- identical to omitting it."
    )


def test_the_buildspec_writes_the_index_gate_into_the_runtime():
    """The READERS' half, and Amplify makes it a separate fact.

    AWS deliberately does not expose an app's environment variables to the SSR
    runtime, so a variable set on the app reaches the readers ONLY if `amplify.yml`
    echoes it into `.env.production`. Setting it in the console and stopping is a
    green build serving an app that reads it as undefined.

    Asserted on the raw text rather than through `spec.runtime_variables()`, which
    derives its list from this same file: a test that read the derivation would move
    with it and could not detect the line being deleted.
    """
    text = BUILDSPEC.read_text()
    written = set(re.findall(
        r'echo\s+"([A-Za-z_][A-Za-z0-9_]*)=\$\{\1\}"\s*>>\s*\.env\.production', text))

    assert written, (
        f"{BUILDSPEC.name} writes NOTHING into .env.production; every variable the "
        f"SSR runtime reads would be undefined after a green build."
    )
    assert GATE in written, (
        f"{BUILDSPEC.name} does not write {GATE} into .env.production. The readers "
        f"then report `indexed: false` on every screen -- which a judge reads as "
        f"'this tenant has no runs', not as 'nothing is configured'."
    )


def test_the_approval_queue_backend_reaches_the_runtime():
    """`POST /api/approvals` spawns a Python that calls `queue.resume`.

    `config.QUEUE_BACKEND` defaults to `memory`, an in-process dict. In a Lambda
    that exits after the response, an approval written there is gone before the
    reviewer's page reloads -- and the route answers 200 either way, which is this
    project's signature defect: a check that cannot distinguish 'did not run' from
    'passed'.
    """
    text = BUILDSPEC.read_text()
    written = set(re.findall(
        r'echo\s+"([A-Za-z_][A-Za-z0-9_]*)=\$\{\1\}"\s*>>\s*\.env\.production', text))

    assert "QUEUE_BACKEND" in written, (
        f"{BUILDSPEC.name} does not write QUEUE_BACKEND into .env.production, so "
        f"the deployed approval path falls back to the in-process memory queue and "
        f"silently opens no gate."
    )


def test_neither_deployed_config_still_names_the_postgres_dsn():
    """`TENANT_DB` in either file means the old gate came back.

    Asserted on both files together rather than in two places: the defect was that
    a writer and its readers were pointed at different stores, and a per-file
    assertion is what let four consistent declarations all be wrong at once.
    """
    for path in (PIPELINE, BUILDSPEC):
        text = path.read_text()
        # Prose may DISCUSS the old name -- both files explain the history at
        # length. Only a real assignment or echo counts.
        offenders = re.findall(
            r'^\s*[-]?\s*(?:echo\s+")?TENANT_DB[=:]', text, re.MULTILINE)
        assert not offenders, (
            f"{path.name} configures TENANT_DB, a Postgres DSN. The DynamoDB-only "
            f"decision means it is never set in the account, so this gate is always "
            f"closed and the run list is silently empty."
        )
