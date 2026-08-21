r"""Pins the ingress module's SECURITY properties as data, not as prose.

Owner: Task 5, fix round 1.

WHY THIS FILE EXISTS
====================
The Terraform for the ingress Lambda shipped with zero test coverage, and a
reviewer then demonstrated four security-weakening mutations that survived every
gate the task had: `pytest`, `ruff`, `terraform fmt -check -recursive` and
`terraform validate` all stayed green while the infrastructure got materially
worse. Reproduced independently before writing any of this:

  * `Resource = ["*"]` on all three IAM statements  -> all gates green
  * `default = -1` + the `validation` block deleted -> all gates green
  * `detail-type = ["github-webhook"]`              -> all gates green

`terraform validate` checks that the configuration is syntactically valid and
internally consistent. It has no opinion about whether an IAM policy is scoped or
a spend cap exists. So the properties whose violation would either widen the
blast radius of a PUBLIC, UNAUTHENTICATED endpoint or silently break the trigger
have to be asserted somewhere, and this is that somewhere.

`trivy config` DOES NOT COVER THIS, and the original task report was wrong to
cite it as IAM evidence. Measured both ways: with the narrow IAM and with
`Resource = ["*"]` on all three statements, `trivy config` reports the SAME three
findings (`AWS-0017`, `AWS-0066`, `AWS-0098` — all LOW, all about CMK encryption
and X-Ray tracing, none about IAM scope). It cannot tell the two states apart.
That claim has been corrected in the report.

WHY IT PARSES THE HCL INSTEAD OF GREPPING IT
============================================
The same trap `tests/test_deploy_workflow.py` documents for YAML applies here,
and worse: `modules/ingress/main.tf` is ~40% comments, and those comments discuss
the exact strings a naive test would assert on. The file says
`granting logs on \`*\`` in a comment explaining why it does NOT do that, and it
contains the literal words `Resource = ["*"]`-adjacent prose about wildcards. A
substring test would be satisfied by the commentary while the policy underneath
it said anything at all.

So `_strip_comments` removes `#`/`//` comments AND heredoc bodies while
respecting string literals, `_block` finds a named block by brace matching, and
assertions read resolved attribute values out of the block bodies. Every helper
that can return nothing asserts that it found something (rule 2) -- a matcher
keyed on a block name that no longer exists must fail loudly, not vacuously pass.

WHAT THIS FILE DELIBERATELY DOES NOT DO
=======================================
It runs no `terraform` subprocess, reaches no AWS endpoint and reads no state. It
is a static assertion over checked-in files, which is what makes it safe to run
in CI with no credentials -- the same reason `tests/test_deploy_workflow.py`
parses workflow YAML rather than dispatching workflows.

It therefore cannot prove the module APPLIES. `terraform validate` covers syntax;
the `plan` job in `.github/workflows/terraform.yml` is the first real check.

A NOTE ON SCOPE, because this module now has two authors
========================================================
Another lane appended an event-target block (API destination + connection) to
`main.tf` after Task 5 landed. These tests are written to assert only on the
blocks Task 5 owns, addressed BY NAME, so they neither depend on nor obstruct
that work. The one exception is `test_no_iam_statement_in_the_module_grants_a_wildcard_resource`,
which sweeps EVERY IAM policy in the file on purpose: a wildcard grant is exactly
as dangerous in an appended block as in an original one, and a test that only
checked the blocks present on the day it was written would go quiet precisely
when the file grew.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE = REPO_ROOT / "infra" / "Terraform" / "modules" / "ingress"
MAIN_TF = MODULE / "main.tf"
VARIABLES_TF = MODULE / "variables.tf"
ENV_MAIN_TF = REPO_ROOT / "infra" / "Terraform" / "environments" / "shared" / "main.tf"

# GitHub's own event name for the Issues subscription. The handler forwards the
# `x-github-event` header verbatim as DetailType, so this string has to match on
# both sides or the rule matches nothing. Pinned on the handler side by
# tests/test_ingress_handler.py::test_the_detail_type_is_githubs_event_name_verbatim.
GITHUB_ISSUES_EVENT_NAME = "issues"


def _strip_comments(text: str) -> str:
    """Blank out `#`/`//` comments and heredoc bodies, preserving string literals.

    Length-preserving (comments become spaces) so any error message that quotes an
    offset still lines up with the real file.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                out.append(text[i : i + 2])
                i += 2
                continue
            if ch == '"':
                in_string = False
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        # Heredocs hold prose that can contain `#` and quotes; skip them wholesale.
        if text.startswith("<<", i):
            match = re.match(r"<<-?([A-Za-z_][A-Za-z0-9_]*)\r?\n", text[i:])
            if match:
                tag = match.group(1)
                end = re.search(r"\n\s*" + re.escape(tag) + r"\s*(\n|$)", text[i:])
                stop = i + (end.end() if end else n - i)
                out.append(" " * (stop - i))
                i = stop
                continue
        if ch == "#" or text.startswith("//", i):
            eol = text.find("\n", i)
            eol = n if eol < 0 else eol
            out.append(" " * (eol - i))
            i = eol
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _code(path: Path) -> str:
    assert path.is_file(), f"{path} is missing -- this test cannot pin anything"
    return _strip_comments(path.read_text())


def _block(text: str, *header: str) -> str | None:
    """Body of `w0 "w1" "w2" { ... }`, brace-matched. None if absent."""
    pattern = (
        r"^\s*"
        + r"\s+".join(
            re.escape(word) if i == 0 else '"' + re.escape(word) + '"'
            for i, word in enumerate(header)
        )
        + r"\s*\{"
    )
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        return None
    start = text.index("{", match.start())
    depth = 0
    for j in range(start, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : j]
    return None


def _nested_object(body: str, name: str) -> str | None:
    """Body of a nested `name = { ... }` object, brace-matched.

    Separate from `_block` because that one anchors at line start to match
    top-level `resource "x" "y" {` headers; an attribute-style object such as
    `detail = {` inside a jsonencode() is indented and assigned.
    """
    match = re.search(re.escape(name) + r"\s*=\s*\{", body)
    if not match:
        return None
    start = body.index("{", match.start())
    depth = 0
    for j in range(start, len(body)):
        if body[j] == "{":
            depth += 1
        elif body[j] == "}":
            depth -= 1
            if depth == 0:
                return body[start + 1 : j]
    return None


def _nested_list(body: str, name: str) -> str | None:
    """Contents of a nested `name = [ ... ]` list, bracket-matched."""
    match = re.search(re.escape(name) + r"\s*=\s*\[", body)
    if not match:
        return None
    start = body.index("[", match.start())
    depth = 0
    for j in range(start, len(body)):
        if body[j] == "[":
            depth += 1
        elif body[j] == "]":
            depth -= 1
            if depth == 0:
                return body[start + 1 : j]
    return None


def _require_block(text: str, *header: str) -> str:
    """_block, but a miss is a failure rather than a silently skipped assertion."""
    body = _block(text, *header)
    assert body is not None, (
        f"no `{' '.join(header)}` block found in the parsed HCL. Either it was "
        "renamed or removed, or the parser broke -- either way every assertion "
        "keyed on it would have passed while testing nothing."
    )
    return body


def _attr(body: str, name: str) -> str | None:
    match = re.search(re.escape(name) + r"\s*=\s*([^\n]+)", body)
    return match.group(1).strip() if match else None


def _require_attr(body: str, name: str) -> str:
    value = _attr(body, name)
    assert value is not None, f"attribute `{name}` not found in the block"
    return value


def _list_attr(body: str, name: str) -> list[str] | None:
    match = re.search(re.escape(name) + r"\s*=\s*\[(.*?)\]", body, re.DOTALL)
    if not match:
        return None
    return [t.strip().strip('"') for t in match.group(1).split(",") if t.strip()]


# ── the parser must not be the weak link ──────────────────────────────────────


def test_the_comment_stripper_actually_removes_comments_and_keeps_code():
    """If this file's own parser is broken, every test below passes vacuously.

    Not a tautology: it feeds in the two shapes that made a grep-based test
    unsafe here -- a wildcard inside a comment, and a `#` inside a string
    literal, which a naive line-splitter would treat as the start of a comment.
    """
    sample = 'a = "keep#me"  # Resource = ["*"] in a comment\nb = ["real"]\n'
    stripped = _strip_comments(sample)

    assert "keep#me" in stripped, "a `#` inside a string literal was eaten as a comment"
    assert '["real"]' in stripped, "real code was removed"
    assert '["*"]' not in stripped, (
        "a wildcard written in a COMMENT survived stripping, so every "
        "wildcard assertion in this file could be satisfied by prose"
    )


def test_the_module_files_this_suite_reads_all_exist():
    for path in (MAIN_TF, VARIABLES_TF, ENV_MAIN_TF):
        assert path.is_file(), f"{path} is missing"


# ── R5: IAM breadth ───────────────────────────────────────────────────────────


def test_no_iam_statement_in_the_module_grants_a_wildcard_resource():
    """The mutation that survived everything: `Resource = ["*"]` on all three.

    Sweeps EVERY `Resource` in every IAM policy in the file rather than the three
    statements that existed when this was written, so a wildcard added in a
    future block is caught too. This function is on a PUBLIC, UNAUTHENTICATED
    endpoint; if it is ever compromised, these ARNs are the whole reachable
    surface.
    """
    code = _code(MAIN_TF)
    resources = re.findall(r"Resource\s*=\s*(\[.*?\]|\"[^\"]*\")", code, re.DOTALL)

    assert resources, (
        "no `Resource` assignments found in main.tf at all. The IAM policy was "
        "renamed, restructured or removed -- this test was pinning nothing."
    )

    wildcards = [r for r in resources if re.fullmatch(r"\[\s*\"\*\"\s*\]|\"\*\"", r.strip())]
    assert not wildcards, (
        f"{len(wildcards)} IAM statement(s) grant `Resource = \"*\"`. This role "
        "belongs to a function on an internet-reachable, unauthenticated Function "
        "URL; every grant must name one ARN. Found: "
        f"{wildcards}\nAll Resource values: {[r.strip() for r in resources]}"
    )


def test_the_secret_grant_names_exactly_the_one_webhook_secret():
    policy = _require_block(_code(MAIN_TF), "resource", "aws_iam_role_policy", "ingress")
    statement = _statement_with_action(policy, "secretsmanager:GetSecretValue")

    resources = _list_attr(statement, "Resource")
    assert resources == ["aws_secretsmanager_secret.webhook.arn"], (
        "the GetSecretValue grant must reference exactly the module's own secret "
        f"ARN and nothing else. got {resources}"
    )


def test_the_putevents_grant_names_exactly_the_one_ingress_bus():
    policy = _require_block(_code(MAIN_TF), "resource", "aws_iam_role_policy", "ingress")
    statement = _statement_with_action(policy, "events:PutEvents")

    resources = _list_attr(statement, "Resource")
    assert resources == ["aws_cloudwatch_event_bus.github.arn"], (
        "the PutEvents grant must reference exactly the module's own bus ARN. "
        f"Granting it on the default bus, or on `*`, lets this function publish "
        f"into other projects' event traffic in a shared account. got {resources}"
    )


def test_the_logs_grant_is_scoped_to_the_modules_own_log_group():
    policy = _require_block(_code(MAIN_TF), "resource", "aws_iam_role_policy", "ingress")
    statement = _statement_with_action(policy, "logs:PutLogEvents")

    resources = _list_attr(statement, "Resource")
    assert resources is not None and len(resources) == 1, resources
    assert "aws_cloudwatch_log_group.ingress.arn" in resources[0], (
        "the logs grant must name this module's own log group. The AWS-managed "
        "AWSLambdaBasicExecutionRole grants logs on `*`, which is why it is not "
        f"attached. got {resources}"
    )


def test_the_function_does_not_get_permission_to_create_log_groups():
    """`CreateLogGroup` cannot be scoped to one group, so granting it re-opens
    the wildcard this module closed. The group is Terraform-managed."""
    policy = _require_block(_code(MAIN_TF), "resource", "aws_iam_role_policy", "ingress")

    assert "logs:CreateLogGroup" not in policy, (
        "the role grants logs:CreateLogGroup. The log group is created by this "
        "module, so the function has no need for it, and the action cannot be "
        "meaningfully scoped to a single group."
    )


def test_the_role_attaches_no_aws_managed_policies():
    """A managed-policy attachment would reintroduce broad grants out of sight of
    the inline policy every other test here reads."""
    code = _code(MAIN_TF)

    attachments = re.findall(r"aws_iam_role_policy_attachment\"?\s+\"", code)
    assert not attachments, (
        "the module attaches a managed policy. Every grant for this role must be "
        "in the inline policy where it can be read and pinned; "
        "AWSLambdaBasicExecutionRole in particular grants logs on `*`."
    )


def _statement_with_action(policy_body: str, action: str) -> str:
    """The one statement object mentioning `action`. Fails if absent or ambiguous."""
    # `policy = jsonencode({ Statement = [ {...}, {...} ] })`, so the statement
    # objects are the depth-1 braces INSIDE the Statement list -- not inside the
    # policy body, where jsonencode's own brace already occupies depth 1.
    statements = _nested_list(policy_body, "Statement")
    assert statements is not None, (
        "no `Statement` list found in the policy body. The policy was "
        "restructured, so every grant assertion keyed on it was vacuous."
    )
    chunks, depth, start = [], 0, None
    for i, ch in enumerate(statements):
        if ch == "{":
            depth += 1
            if depth == 1:
                start = i
        elif ch == "}":
            if depth == 1 and start is not None:
                chunks.append(statements[start : i + 1])
            depth -= 1
    matching = [c for c in chunks if action in c]

    assert matching, (
        f"no IAM statement mentions `{action}`. It was renamed or removed, so "
        "any assertion about its Resource would have been vacuous. "
        f"statements found: {len(chunks)}"
    )
    assert len(matching) == 1, (
        f"{len(matching)} statements mention `{action}`; expected exactly one so "
        "this test reads an unambiguous grant"
    )
    return matching[0]


# ── R4: the spend cap ─────────────────────────────────────────────────────────


def test_the_reserved_concurrency_default_caps_spend():
    """`-1` removes the cap and `0` disables the function. Both are legal Lambda
    values, and the brief calls this cap required rather than tuning: the URL is
    public, so anyone can drive invocations."""
    variable = _require_block(_code(VARIABLES_TF), "variable", "reserved_concurrency")
    default = _require_attr(variable, "default")

    assert re.fullmatch(r"\d+", default), (
        f"reserved_concurrency default is `{default}`. A negative default removes "
        "the concurrency cap entirely, which is the only structural limit on what "
        "an anonymous flood against a public Function URL can cost."
    )
    assert int(default) >= 1, (
        f"reserved_concurrency default is {default}; 0 disables the function"
    )


def test_the_reserved_concurrency_variable_rejects_the_uncapped_values():
    """The default alone is not the property -- a caller can override it. The
    `validation` block is what makes `-1` unreachable from the outside."""
    variable = _require_block(_code(VARIABLES_TF), "variable", "reserved_concurrency")
    validation = _block(variable, "validation")

    assert validation is not None, (
        "the reserved_concurrency variable has no `validation` block, so a caller "
        "can pass -1 and remove the spend cap on a public endpoint without any "
        "gate objecting."
    )
    condition = _require_attr(validation, "condition")
    assert ">=" in condition or ">" in condition, (
        f"the validation condition does not impose a lower bound: {condition}"
    )


def test_the_function_actually_wires_the_reserved_concurrency_variable():
    """A validated variable that nothing reads is a cap in name only."""
    function = _require_block(_code(MAIN_TF), "resource", "aws_lambda_function", "ingress")
    value = _attr(function, "reserved_concurrent_executions")

    assert value is not None, (
        "aws_lambda_function.ingress sets no reserved_concurrent_executions, so "
        "the function scales to the account limit under an anonymous flood."
    )
    assert value == "var.reserved_concurrency", (
        f"expected the validated variable to be wired through; got `{value}`"
    )


def test_the_log_retention_variable_rejects_never_expire():
    """`0` means never expire. This function's log volume is driven by public
    traffic, so an unbounded retention is an unbounded bill."""
    variable = _require_block(_code(VARIABLES_TF), "variable", "log_retention_days")
    validation = _block(variable, "validation")

    assert validation is not None, (
        "log_retention_days has no validation block, so 0 (never expire) is "
        "reachable"
    )
    default = _require_attr(variable, "default")
    assert int(default) > 0, f"log_retention_days default is {default} (never expire)"


# ── R6: the cross-file contract ───────────────────────────────────────────────


def test_the_rule_matches_githubs_own_issues_event_name():
    """THE OTHER HALF OF A CROSS-FILE CONTRACT.

    The handler forwards `x-github-event` verbatim as DetailType; this rule must
    match that exact string. Changing either side alone means the bus accepts
    every event, no rule fires, no pipeline starts, and NOTHING TURNS RED --
    which is precisely how this mutation survived the original suite. The handler
    side is pinned by
    tests/test_ingress_handler.py::test_the_detail_type_is_githubs_event_name_verbatim;
    this is the Terraform side.
    """
    rule = _require_block(
        _code(MAIN_TF), "resource", "aws_cloudwatch_event_rule", "issue_opened"
    )
    detail_types = _list_attr(rule, "detail-type")

    assert detail_types is not None, (
        "the rule's event_pattern has no `detail-type`, so it matches every "
        "event on the bus rather than issue events"
    )
    assert detail_types == [GITHUB_ISSUES_EVENT_NAME], (
        f"the rule matches detail-type {detail_types}, but the handler publishes "
        f"GitHub's event name verbatim, which for the Issues subscription is "
        f"{GITHUB_ISSUES_EVENT_NAME!r}. As written this rule matches nothing and "
        "no issue will ever start a pipeline run."
    )


def test_the_rule_filters_to_opened_issues_only():
    """Every Issues delivery (edited, labeled, closed) reaches the bus; only
    `opened` should start a run. Filtering here rather than in the handler keeps
    "never saw it" distinguishable from "saw it and ignored it"."""
    rule = _require_block(
        _code(MAIN_TF), "resource", "aws_cloudwatch_event_rule", "issue_opened"
    )
    detail = _nested_object(rule, "detail")
    assert detail is not None, "the rule's event_pattern has no `detail` object"

    actions = _list_attr(detail, "action")
    assert actions == ["opened"], (
        f"expected the rule to match only opened issues; got action={actions}. "
        "Matching every action means a closed or edited issue starts a pipeline "
        "run too."
    )


def test_the_rule_is_bound_to_the_modules_own_bus_not_the_default_bus():
    """The account is shared with the rosettacloud_* projects. A rule on the
    default bus sits in their traffic."""
    rule = _require_block(
        _code(MAIN_TF), "resource", "aws_cloudwatch_event_rule", "issue_opened"
    )
    bus = _attr(rule, "event_bus_name")

    assert bus is not None and "aws_cloudwatch_event_bus.github" in bus, (
        f"the rule is not bound to this module's dedicated bus: {bus}"
    )


# ── the properties that make the endpoint's exposure deliberate ───────────────


def test_the_function_url_is_unauthenticated_deliberately_and_visibly():
    """`NONE` is correct -- GitHub cannot sign SigV4 -- and it is the single most
    consequential line in the module, so it is pinned rather than left to drift
    silently in either direction. If someone changes it to AWS_IAM, every GitHub
    delivery starts failing with a 403 that looks like a signature problem; this
    test makes that a deliberate act with a failing test to acknowledge.
    """
    url = _require_block(
        _code(MAIN_TF), "resource", "aws_lambda_function_url", "ingress"
    )
    auth = _require_attr(url, "authorization_type")

    assert auth == '"NONE"', (
        f"authorization_type is {auth}. It must be NONE (GitHub cannot sign a "
        "SigV4 request), and the HMAC in the handler is therefore the only "
        "access control -- see the module header."
    )


def test_terraform_never_writes_the_webhook_secrets_value():
    """A value written here lands in S3 state, readable by everyone with state
    access, and drifts on rotation. The human writes it once (step 6)."""
    secret = _require_block(
        _code(MAIN_TF), "resource", "aws_secretsmanager_secret", "webhook"
    )

    assert "secret_string" not in secret, (
        "the secret resource sets a value. Terraform must create the container "
        "only; a secret in configuration is a secret in state."
    )
    # Scoped to the WEBHOOK secret deliberately. A `data` source reading some
    # OTHER secret's version is a different thing entirely (and another lane has
    # since added one for the dispatch token); what must never exist is a
    # managed `resource` version for this secret, which would put the webhook
    # secret's value in configuration and therefore in state.
    code = _code(MAIN_TF)
    managed_versions = re.findall(
        r'resource\s+"aws_secretsmanager_secret_version"\s+"(\w+)"', code
    )
    assert not managed_versions, (
        "the module declares a managed secret VERSION resource "
        f"({managed_versions}), which means Terraform owns a secret's value and "
        "it will be committed to state."
    )


def test_the_function_receives_the_secrets_arn_and_no_secret_material():
    function = _require_block(_code(MAIN_TF), "resource", "aws_lambda_function", "ingress")
    environment = _block(function, "environment")
    assert environment is not None, "the function declares no environment block"

    arn = _attr(environment, "WEBHOOK_SECRET_ARN")
    assert arn is not None and "aws_secretsmanager_secret.webhook.arn" in arn, (
        f"WEBHOOK_SECRET_ARN must be the secret's ARN; got {arn}"
    )
    for forbidden in ("WEBHOOK_SECRET ", "SECRET_STRING", "secret_string"):
        assert forbidden not in environment, (
            f"the function's environment carries {forbidden!r}, which would put "
            "secret material in the Lambda configuration and in state"
        )


def test_the_handler_source_path_resolves_to_the_real_handler_file():
    """The module zips `${var.handler_source_dir}/handler.py`. If the environment
    passes a path that does not exist, the failure is at plan time in CI rather
    than here -- so resolve it now, from the value actually wired in."""
    env_module = _require_block(_code(ENV_MAIN_TF), "module", "ingress")
    raw = _require_attr(env_module, "handler_source_dir")

    # `"${path.root}/../../../ingress"` -> the repo-relative directory.
    relative = raw.strip('"').replace("${path.root}", "infra/Terraform/environments/shared")
    resolved = Path(relative).resolve() if Path(relative).is_absolute() else (
        (REPO_ROOT / relative).resolve()
    )

    assert (resolved / "handler.py").is_file(), (
        f"handler_source_dir={raw} resolves to {resolved}, which holds no "
        "handler.py. `terraform plan` would fail on the archive_file data source."
    )


def test_the_lambda_handler_entrypoint_matches_the_zips_layout():
    """`data.archive_file` uses `source_file`, so the zip has handler.py at its
    ROOT. The entrypoint must therefore be `handler.handler`, not a package path."""
    function = _require_block(_code(MAIN_TF), "resource", "aws_lambda_function", "ingress")
    entrypoint = _require_attr(function, "handler")

    assert entrypoint == '"handler.handler"', (
        f"handler is {entrypoint}. The archive is built from a single source_file, "
        "so handler.py sits at the zip root and any dotted package prefix would "
        "fail at runtime with Unable to import module."
    )


@pytest.mark.parametrize(
    "resource",
    [
        ("aws_lambda_function", "ingress"),
        ("aws_lambda_function_url", "ingress"),
        ("aws_secretsmanager_secret", "webhook"),
        ("aws_cloudwatch_event_bus", "github"),
        ("aws_cloudwatch_event_rule", "issue_opened"),
        ("aws_iam_role", "ingress"),
        ("aws_iam_role_policy", "ingress"),
        ("aws_cloudwatch_log_group", "ingress"),
    ],
)
def test_every_resource_task_five_owns_is_still_present(resource):
    """A rename that removed one of these would make several tests above silently
    stop testing. This is the tripwire for that."""
    kind, name = resource
    assert _block(_code(MAIN_TF), "resource", kind, name) is not None, (
        f"resource {kind}.{name} is gone from the ingress module. Tests keyed on "
        "it would pass while asserting nothing."
    )
