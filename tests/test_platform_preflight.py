"""LANE N, N4 — the three new preflight checks, as tests.

Preflight makes live AWS and database calls, so these tests do NOT run the checks.
They pin the four properties that can be verified hermetically, and every one of them
is a property whose absence would make a check silently wrong rather than broken:

  1. THE SECOND DECLARATIONS AGREE WITH TERRAFORM. `preflight_platform` names the
     worker's task role, and `preflight.py` defaults the cluster and service names --
     three strings Terraform owns. `preflight_platform.py`'s own comment promises this
     test by name, and a promise in a comment is what this repository calls a gap
     recorded only in a comment.
  2. THE CHECKS ARE REACHED. Phase 3 shipped four capabilities with no caller; a
     check absent from `main`'s list is a check that cannot fail.
  3. A SKIP IS LOUD. "did not run" and "passed" must never read alike, and these
     three checks skip in the ordinary case -- so the skip text is the thing most
     likely to be read.
  4. THE DSN IS NEVER PRINTED. It carries a password.

WHAT THESE DELIBERATELY DO NOT ASSERT: that a check gives the right answer. Only a
live account and a live database can say that, which is the whole reason preflight is
a script an operator runs rather than a test. Both directions were verified by hand
and the measurements are in the commit messages and in each module's docstring.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PREFLIGHT = REPO_ROOT / "scripts" / "preflight.py"
PLATFORM_CHECKS = REPO_ROOT / "scripts" / "preflight_platform.py"
RLS_CHECK = REPO_ROOT / "scripts" / "preflight_rls.py"
MODULE = REPO_ROOT / "infra" / "Terraform" / "modules" / "platform"


def _source(path: Path) -> str:
    assert path.is_file(), f"{path} is missing"
    return path.read_text()


# ── 1. THE SECOND DECLARATIONS MUST AGREE WITH TERRAFORM ─────────────────────


def test_the_worker_task_role_name_matches_the_terraform_resource():
    """`preflight_platform.WORKER_TASK_ROLE` against `modules/platform/iam.tf`.

    THIS IS THE TEST THAT MODULE'S COMMENT PROMISES BY NAME. It is a second
    declaration of a name Terraform owns, and CLAUDE.md's rule is that a second
    declaration needs something able to detect a change in the first -- otherwise both
    copies keep agreeing with themselves while one has moved, and check 5 silently
    simulates a role that does not exist. Which it reports as SKIPPED. Which reads as
    a pass.
    """
    checks = _source(PLATFORM_CHECKS)
    declared = re.search(r'WORKER_TASK_ROLE = f"[^"]*role/([^"]+)"', checks)
    assert declared, "preflight_platform declares no WORKER_TASK_ROLE"
    role_name = declared.group(1)

    iam = _source(MODULE / "iam.tf")
    # The module builds the name from `var.name`, which the root passes as
    # `local.name = "theagentorg-shared"`. So the suffix is what must match.
    suffix = re.search(r'name = "\$\{var\.name\}-(worker-task-role)"', iam)
    assert suffix, (
        "modules/platform/iam.tf no longer declares a role named "
        "`${var.name}-worker-task-role`; check 5 would simulate a role that does not "
        "exist and report SKIPPED, which reads as a pass"
    )
    assert role_name.endswith(suffix.group(1)), (
        f"preflight names the role {role_name!r} while Terraform creates "
        f"`<name>-{suffix.group(1)}`. Check 5 would simulate a nonexistent role, take "
        f"its role-absent branch, and print SKIPPED -- so the worker's Bedrock grant "
        f"would go unchecked with preflight reporting OK."
    )

    root = _source(REPO_ROOT / "infra" / "Terraform" / "environments" / "shared" / "main.tf")
    assert 'name       = "theagentorg-shared"' in root or 'name = "theagentorg-shared"' in root, (
        "the root module no longer passes `theagentorg-shared` as `name`, so the "
        "role's real name differs from the one preflight assembles"
    )


def test_the_cluster_and_service_defaults_match_terraform_and_the_workflow():
    """THREE places name the cluster and service, and all three must agree.

    `preflight.py`'s argparse defaults, `modules/platform/ecs.tf`'s resources, and
    `deploy-platform.yml`'s env. A mismatch in any pair means check 6 reports SKIPPED
    for a service that IS running, or the workflow refuses a redeploy of a service
    that exists -- both of which read as "not deployed yet".
    """
    preflight = _source(PREFLIGHT)
    cluster = re.search(r'"--ecs-cluster", default="([^"]+)"', preflight)
    service = re.search(r'"--ecs-service", default="([^"]+)"', preflight)
    assert cluster and service, "preflight declares no --ecs-cluster/--ecs-service"

    ecs = _source(MODULE / "ecs.tf")
    assert 'name = "${var.name}-platform"' in ecs, (
        "the cluster is no longer named `${var.name}-platform`"
    )
    assert 'name            = "${var.name}-worker"' in ecs, (
        "the service is no longer named `${var.name}-worker`"
    )
    assert cluster.group(1) == "theagentorg-shared-platform", (
        f"preflight defaults the cluster to {cluster.group(1)!r}, which is not "
        f"`theagentorg-shared` + the module's `-platform`. Check 6 would report "
        f"SKIPPED for a running worker."
    )
    assert service.group(1) == "theagentorg-shared-worker"

    workflow = _source(REPO_ROOT / ".github" / "workflows" / "deploy-platform.yml")
    for name in (cluster.group(1), service.group(1)):
        assert name in workflow, (
            f"deploy-platform.yml does not name {name!r}, so the workflow and "
            f"preflight disagree about which service exists"
        )


def test_the_ecr_repository_name_agrees_between_the_workflow_and_the_module():
    """The workflow pushes to a registry Terraform creates. One literal, two files."""
    workflow = _source(REPO_ROOT / ".github" / "workflows" / "deploy-platform.yml")
    declared = re.search(r"ECR_REPO: (\S+)", workflow)
    assert declared, "deploy-platform.yml declares no ECR_REPO"
    assert declared.group(1) == "theagentorg-shared-worker", (
        f"the workflow pushes to {declared.group(1)!r}"
    )
    main = _source(MODULE / "main.tf")
    assert 'name                 = "${var.name}-worker"' in main, (
        "the module no longer creates a repository named `${var.name}-worker`, so the "
        "workflow's push target does not exist and the build fails with "
        "\"repository does not exist\" rather than naming the cause"
    )


# ── 2. THE CHECKS MUST BE REACHED ────────────────────────────────────────────


@pytest.mark.parametrize("function_name", [
    "check_the_worker_role_can_invoke_the_model",
    "check_the_worker_service_matches_the_image_it_should_run",
    "check_the_dsn_role_is_bound_by_rls",
])
def test_every_new_check_is_actually_called_by_main(function_name):
    """A FEATURE NOTHING CALLS DOES NOT EXIST, whatever the suite says.

    Phase 3 shipped four capabilities with no caller -- scoring's two call sites, the
    usage payload, `accessors.record_run`'s writer, and all of `testgen`. Each was
    complete, tested, and reached by nothing.

    Asserted over the AST rather than by grep, because `preflight.py` and both check
    modules discuss these names at length in prose: a substring search matches the
    comment explaining the wiring while the call is absent. That failure is recorded
    twice in CLAUDE.md.
    """
    tree = ast.parse(_source(PREFLIGHT))
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
    # ANTI-VACUITY: this walk must find the ORIGINAL checks too, or it is matching
    # nothing and every parametrised case passes for the wrong reason.
    assert "check_iam_can_invoke_the_model" in called, (
        "the AST walk found none of preflight's original checks, so it is not reading "
        "call sites and this test would pin nothing"
    )
    assert function_name in called, (
        f"`{function_name}` is never CALLED in preflight.py. It is correct, tested, "
        f"and reached by nothing -- the second pattern this repository names, found "
        f"four times in Phase 3 alone. Add it to `main`'s checks list."
    )


def test_the_new_checks_are_not_gated_behind_a_flag():
    """They must run unconditionally and skip loudly, never be omitted.

    A check absent from the run and a check that passed are indistinguishable in the
    output. `--skip-invoke` exists for check 3 because that one costs model tokens;
    checks 5-7 cost nothing when their subject is absent, so there is no argument for
    a flag -- and adding one would let a demo pass with tenant isolation unexamined.
    """
    tree = ast.parse(_source(PREFLIGHT))
    main = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    # Find the `checks.extend([...])` that adds them, and assert it is not inside an
    # `if`. Walking the body statements directly rather than `ast.walk`, because the
    # question is precisely about nesting.
    def extends_at_top_level(body: list[ast.stmt]) -> bool:
        for statement in body:
            if not isinstance(statement, ast.Expr):
                continue
            call = statement.value
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "extend"):
                return True
        return False

    assert extends_at_top_level(main.body), (
        "the new checks are not added at the top level of `main` -- they are inside a "
        "conditional, so some invocation of preflight silently does not run them. A "
        "check absent from the output cannot be told from one that passed."
    )


# ── 3. A SKIP MUST BE LOUD ───────────────────────────────────────────────────


@pytest.mark.parametrize(("path", "needle"), [
    ("scripts/preflight_platform.py", "NOTHING HERE CHECKED"),
    ("scripts/preflight_rls.py", "NOTHING HERE CHECKED"),
])
def test_every_skip_states_what_it_did_not_check(path, needle):
    """The three new checks skip in the ORDINARY case, so the skip text is what an
    operator actually reads.

    `runtime_enabled` defaults false and `QUEUE_DSN` defaults empty, so on any machine
    that has not deployed the worker all three skip. A skip that said only "skipped"
    would let a demo run against a green preflight having examined none of the
    platform -- which is the defect this whole script exists to prevent, arriving
    through the script itself.
    """
    source = _source(REPO_ROOT / path)
    assert "SKIPPED" in source, f"{path} has no skip branch at all"
    assert needle in source, (
        f"{path} skips without naming what went unchecked. \"did not run\" and "
        f"\"passed\" must never read alike -- and these checks skip by default, so "
        f"this is the common case rather than the edge."
    )


def test_a_postgres_dsn_with_no_driver_is_a_refusal_and_not_a_skip():
    """The asymmetry that matters in check 7.

    No DSN is a SKIP: nobody asked for the multi-tenant path. A Postgres DSN with
    psycopg absent is a REFUSAL: the caller DID ask, and answering `skipped` there
    would report green for the one question whose wrong answer returns rows.

    Same direction as `QUEUE_BACKEND=sqs` raising rather than falling back to memory,
    and as `budgets.check` refusing a tenant with no budget row.
    """
    source = _source(RLS_CHECK)
    driver_branch = source.split("importlib.import_module(\"psycopg\")", 1)
    assert len(driver_branch) == 2, "check 7 no longer imports psycopg by name"
    after = driver_branch[1][:900]
    assert "raise CheckFailed" in after, (
        "an absent psycopg with a Postgres DSN supplied does not RAISE. It would be "
        "reported as a skip, so preflight would print OK having never examined tenant "
        "isolation on a deployment that asked for it."
    )
    assert "REFUSED RATHER THAN SKIPPED" in after, (
        "the refusal does not explain why it is not a skip, which is the part a reader "
        "will question"
    )


# ── 4. THE DSN MUST NEVER BE PRINTED ─────────────────────────────────────────


def test_the_dsn_is_redacted_everywhere_it_is_reported():
    """That string carries a database password.

    Asserted over the AST: every f-string in the module that interpolates the DSN must
    go through `_redact`. A grep would be satisfied by the docstring saying the value
    is never printed, which is this repository's most-repeated test failure.
    """
    tree = ast.parse(_source(RLS_CHECK))
    bare_uses = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        for value in node.values:
            if not isinstance(value, ast.FormattedValue):
                continue
            # A bare `{dsn}` is the defect; `{_redact(dsn)}` is correct.
            if isinstance(value.value, ast.Name) and value.value.id == "dsn":
                bare_uses.append(ast.unparse(node)[:80])
    assert not bare_uses, (
        f"the DSN is interpolated without redaction in {len(bare_uses)} place(s): "
        f"{bare_uses}. That value carries a database password, and preflight's output "
        f"is pasted into reports and commit messages."
    )
    # ANTI-VACUITY: the walk must find the redacted uses, or it is reading nothing.
    redacted = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_redact"
    ]
    assert redacted, (
        "no call to `_redact` anywhere, so either the DSN is never reported (and this "
        "test pins nothing) or it is reported raw"
    )


def test_the_redactor_actually_removes_a_password():
    """The one EXECUTED assertion in this file, and it is worth the exception.

    A redactor is a security control, and a structural check that `_redact` is called
    says nothing about whether it works. `test_platform_expression.py`'s lesson one
    layer over: only running it revealed that the helper was wrong.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_rls_probe", RLS_CHECK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    hidden = module._redact("postgresql://agentorg:s3cr3t-pw@db.example:5432/agentorg")
    assert "s3cr3t-pw" not in hidden, f"the password survived redaction: {hidden}"
    assert "agentorg" in hidden and "db.example:5432" in hidden, (
        f"redaction removed the parts an operator needs to identify the database: "
        f"{hidden}"
    )
    # A DSN with no password must survive intact -- the form used in every local probe.
    plain = module._redact("postgresql://agentorg@127.0.0.1:54329/agentorg")
    assert plain == "postgresql://agentorg@127.0.0.1:54329/agentorg", (
        f"a password-less DSN was mangled: {plain}"
    )
