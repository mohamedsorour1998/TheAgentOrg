"""The Amplify app, as DATA. No boto3, no I/O except reading `amplify.yml`.

Same split as `infra/cognito/spec.py`, for the same reason: everything worth
pinning about this deployment -- which variables reach the SSR runtime, that no
credential is among them, that a converge cannot delete a key the console wrote
-- is a property of the configuration rather than of the API call, and a module
that builds an AWS client cannot be imported by a hermetic suite.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REGION = os.getenv("AWS_REGION", "us-east-1")

APP_NAME = "theagentorg-web"
BRANCH = "main"

# **`WEB_COMPUTE`, NOT `WEB`.** The live enum is `WEB`, `WEB_DYNAMIC`,
# `WEB_COMPUTE`, and `WEB` is a STATIC host: no route handlers, no proxy. On a
# static platform `web/app/api/**` simply would not exist -- so `POST
# /api/approvals`, the one surface in this repository that can open a human gate
# over a network, and all three Cognito auth routes would be absent, while the
# build and the deploy both reported success. Verify `platform` with `get-app`
# BEFORE reading a green build as a working app.
PLATFORM = "WEB_COMPUTE"

# THE SSR RUNTIME'S OWN CREDENTIAL, and until 2026-09-15 the app had none.
#
# MEASURED that day: `computeRoleArn` was null on both the app and the branch,
# and the only role the app carried was `iamServiceRoleArn` ->
# `AmplifySSRLoggingRole-*`, whose entire policy is four CloudWatch actions. So the
# SSR runtime could write logs and reach nothing else -- and step 8 had already
# repointed `web/lib/reader/*.py` at DynamoDB, onto a runtime with no credential
# able to read it. Correct code that cannot run, with every gate green: `next
# build` compiles the readers and no test in either suite can see an IAM policy.
#
# THE TWO FIELDS ARE NOT INTERCHANGEABLE. `iamServiceRoleArn` is what Amplify
# assumes on the app's behalf for logging; `computeRoleArn` is what the SSR
# Lambda runs AS. Setting the first and expecting data access is the mistake this
# constant exists to prevent.
#
# The role holds NO data access of its own -- only `sts:AssumeRole` +
# `sts:TagSession` on the tenant-scoped role -- so the chain stays intact:
#   Cognito custom:tenant (immutable) -> session tag -> LeadingKeys -> DynamoDB
COMPUTE_ROLE_NAME = "theagentorg-shared-amplify-compute"

# The subdirectory holding the Next.js app. Amplify needs this in two places that
# must agree -- `amplify.yml`'s `appRoot` and the `AMPLIFY_MONOREPO_APP_ROOT`
# variable -- and AWS's own documentation says the variable "must exist, and have
# the same value as" the key. One constant, and `parsed_app_root()` reads the
# other half back out of the file so the two cannot drift.
APP_ROOT = "web"

# Amplify stores this per branch and uses it to decide how to host the artifact.
# An arbitrary string is accepted -- the reference deployment measured
# `Nonsense - NotAFramework` being stored verbatim -- so it is not validated for
# us and has to be right.
FRAMEWORK = "Next.js - SSR"

# The repository root, from this file: `infra/amplify/spec.py` -> three up.
REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_SPEC_PATH = REPO_ROOT / "amplify.yml"

# Keys this module does not own and must never delete. **MEASURED ON THE
# REFERENCE DEPLOYMENT, TWICE.** `update_app(environmentVariables=...)` is a FULL
# REPLACE of the whole map, and the Amplify console writes its own keys into that
# same map -- so a converge that sends a rebuilt map silently removes them. A map
# rebuilt from a stale read dropped `AMPLIFY_MONOREPO_APP_ROOT`,
# `AMPLIFY_DIFF_DEPLOY` and `_LIVE_UPDATES`, and the next build failed 59 seconds
# in, at clone time, before any phase ran, reporting `Cannot read 'next' version
# in package.json` -- which reads like a repository or packaging fault rather
# than like a variable somebody deleted. That gap between the symptom and the
# cause is what makes this worth a named constant.
PRESERVED_PREFIXES = ("AMPLIFY_", "_LIVE_UPDATES")

# **AMPLIFY REFUSES THE WHOLE `AWS` PREFIX**, measured on the reference:
# `BadRequestException: Environment variables cannot start with the reserved
# prefix "AWS".` for `AWS_REGION`, `AWS_DEFAULT_REGION` and `AWS_ACCESS_KEY_ID`
# alike. Checked here so the failure names the variable rather than arriving from
# the API three calls into a provisioning run.
RESERVED_PREFIX = "AWS"


def build_spec() -> str:
    """The committed `amplify.yml`, verbatim.

    **READ, NEVER RESTATED.** The reference deployment carries the spec twice --
    a committed file and a `build_spec()` that rebuilds the same YAML from a
    command list -- and those two can drift. Here the module sends the bytes of
    the file the operator reviews, so the console copy, the committed copy and
    the tested copy are one thing. `tests/test_infra_amplify.py` asserts this
    function returns the file's contents rather than a rendering of them.
    """
    return BUILD_SPEC_PATH.read_text(encoding="utf-8")


def parsed_app_root(source: str | None = None) -> str:
    """`appRoot` as it appears in the committed spec.

    Read back rather than assumed: `AMPLIFY_MONOREPO_APP_ROOT` and this value
    must be equal, and if they are not the build dies at clone time with a
    message about `package.json`. Two constants that must agree, with only one
    of them typed here.
    """
    text = build_spec() if source is None else source
    match = re.search(r"^\s*-\s*appRoot:\s*(\S+)\s*$", text, re.MULTILINE)
    if match is None:
        raise RuntimeError(
            f"{BUILD_SPEC_PATH} declares no `appRoot`. Without the "
            "applications:/appRoot: form Amplify refuses the spec outright when "
            "AMPLIFY_MONOREPO_APP_ROOT is set."
        )
    return match.group(1)


def runtime_variables(source: str | None = None) -> tuple[str, ...]:
    """Every variable the buildspec writes into `.env.production`, in order.

    **PARSED OUT OF `amplify.yml` RATHER THAN DECLARED BESIDE IT.** The map this
    module sends to `update_app` and the list the build writes into
    `.env.production` are two halves of one fact: a variable set on the app but
    not echoed never reaches the runtime, and a variable echoed but not set
    writes an EMPTY value into the file, which `readEnv`-style code then reads as
    configured-and-blank rather than as absent. Deriving one from the other means
    a new variable is one edit in one file.

    Deliberately anchored on the exact `>> .env.production` shape rather than on
    any `echo`, so an unrelated `echo` in the build phase cannot be mistaken for
    a variable.
    """
    text = build_spec() if source is None else source
    found = re.findall(
        r'echo\s+"([A-Za-z_][A-Za-z0-9_]*)=\$\{\1\}"\s*>>\s*\.env\.production',
        text,
    )
    if not found:
        raise RuntimeError(
            f"{BUILD_SPEC_PATH} writes no variables into .env.production. "
            "Amplify does not expose its environment to the SSR runtime, so an "
            "app provisioned from this spec would read every value as undefined "
            "and refuse every session on a green deploy."
        )
    return tuple(found)


def environment_variables(**values: str) -> dict[str, str]:
    """The variables this module owns, with every value non-empty.

    Not the complete map that reaches `update_app` -- see `merged_environment`.
    A missing or blank value RAISES rather than shipping `""`: the reference
    deployment's draft carried `""` placeholders, and an interrupted run could
    then pin a URL blank forever, which sends every reviewer's sign-in redirect
    somewhere nobody intended while the console shows a key that is present.
    """
    owned = {
        # Must equal `amplify.yml`'s `appRoot`; read back rather than retyped.
        "AMPLIFY_MONOREPO_APP_ROOT": parsed_app_root(),
    }
    for name in runtime_variables():
        value = str(values.get(name, "")).strip()
        if not value:
            raise RuntimeError(
                f"{name} is written into .env.production by amplify.yml and no "
                "non-empty value was supplied. Setting it blank would deploy an "
                "app that reads it as configured-and-empty."
            )
        owned[name] = value
    return owned


def refuse_reserved_and_malformed(variables: dict[str, str]) -> None:
    """Raise on any key Amplify will refuse, or that an exact lookup will miss.

    Two different failures, and the second is the instructive one.

    A key starting with `AWS` is refused by the API with a message that names the
    prefix -- annoying, and self-explanatory.

    **A KEY WITH WHITESPACE IN IT IS ACCEPTED AND READS AS ABSENT.** The
    reference deployment had two entered by hand -- `GRACE_TABLE_NAME  ` and
    `GRACE_ESCALATION _INDEX` -- and an exact-key lookup found neither. A
    value-trimming helper cannot trim a malformed KEY, so the symptom is "the
    variable is not set" on a variable the console plainly shows. Checked here
    because this module is the last place that can see it.
    """
    for key in variables:
        if key.upper().startswith(RESERVED_PREFIX):
            raise RuntimeError(
                f"{key!r} starts with the reserved prefix {RESERVED_PREFIX!r}; "
                "Amplify refuses the whole prefix, including AWS_REGION. The "
                "SSR runtime resolves a region ambiently and takes credentials "
                "from its execution role."
            )
        if key != key.strip() or any(c.isspace() for c in key):
            raise RuntimeError(
                f"{key!r} carries whitespace. Amplify stores it verbatim and an "
                "exact-key lookup then reads it as ABSENT, so the symptom is a "
                "variable that is plainly present in the console and undefined "
                "in the app."
            )


def merged_environment(
    existing: dict[str, str] | None, **values: str
) -> dict[str, str]:
    """This module's variables merged OVER a fresh read -- never a rebuilt map.

    The merge is one-directional and the ORDER is the whole point: existing keys
    survive, ours win where they overlap. See `PRESERVED_PREFIXES` for what a
    rebuilt map cost the reference deployment.
    """
    merged = {k: v for k, v in (existing or {}).items() if v is not None}
    merged.update(environment_variables(**values))
    refuse_reserved_and_malformed(merged)
    return merged


def unowned_keys(existing: dict[str, str] | None) -> set[str]:
    """Keys on the app this module does not own, reported so a converge says out
    loud what it is carrying forward rather than silently depending on it."""
    owned = {"AMPLIFY_MONOREPO_APP_ROOT", *runtime_variables()}
    return {
        key
        for key in (existing or {})
        if key not in owned
        and any(key.startswith(prefix) for prefix in PRESERVED_PREFIXES)
    }
