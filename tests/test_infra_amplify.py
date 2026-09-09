"""The Amplify build spec and the app's environment. Owner: Lane Q.

WHAT THIS FILE DEFENDS, AND WHY IT IS NOT PARANOIA. The reference deployment at
`~/sorour/AgentsforHumansHackathon/` recorded **five defects that all survived a green
build**, and its own summary is the reason this file exists: *"a green build proves
nothing about a running SSR app."* Builds 1-4 reported SUCCEED or failed for reasons that
misdescribed themselves.

Three of those five are structural and can be pinned here without an AWS call:

  * the monorepo spec form, where the wrong shape fails at CLONE time with a message
    about `package.json`;
  * the reserved `AWS` prefix, which Amplify rejects outright;
  * `update_app(environmentVariables=...)` being a FULL REPLACE, where a map built from
    a stale read deleted `AMPLIFY_MONOREPO_APP_ROOT` and the next build died with
    `Cannot read 'next' version in package.json` — a deleted variable that reads as a
    packaging problem.

AND ONE THING CHANGED UNDER THIS LANE THAT MAKES IT MATTER MORE. `next build` used to
refuse without four environment variables, because `authConfig` called `sessionPool()` at
module scope. Lane P removed that call, and measured the build at **exit 0 with every
variable unset**. So the build no longer enforces the list at all: a missing variable now
surfaces as a *running* app refusing every session, which reads as "sign-in broke" rather
than "a variable is unset". These assertions are what is left standing in its place.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.amplify import spec

# Lane P's authoritative runtime list, RESTATED rather than imported. This is the
# handoff between two lanes, and a test that read it from the same place the code reads
# it would pass while both moved together — the second-declaration argument this
# repository applies to `COMMENT_HEADER` and to `REAL_SCANNER_LINES`.
#
# The three Lane P names that are deliberately NOT here are as important as the four
# that are: COGNITO_CLIENT_SECRET (the client is provisioned public, so there is no
# secret), and DATABASE_URL / TENANT_DB (a DSN carries a password and does not belong in
# a committed file). `amplify.yml` records both exclusions in prose.
EXPECTED_RUNTIME_VARIABLES = frozenset({
    "COGNITO_ISSUER",
    "COGNITO_CLIENT_ID",
    "COGNITO_DOMAIN",
    "AUTH_URL",
})


# ── the monorepo form: both settings, or neither ──────────────────────────────

def test_the_buildspec_uses_the_applications_form():
    """`AMPLIFY_MONOREPO_APP_ROOT` and the `applications:` key are A PAIR.

    Measured in the reference: with the variable set, a flat top-level `frontend:` spec
    is rejected as `CustomerError: Monorepo spec provided without "applications" key`;
    without the variable, the build cannot find Next and fails at clone time with
    `Cannot read 'next' version in package.json`. **Both fail at clone time**, and
    neither message names the setting that caused it.
    """
    parsed = yaml.safe_load(spec.build_spec())

    assert "applications" in parsed, (
        "the buildspec is in the flat single-app form. With AMPLIFY_MONOREPO_APP_ROOT "
        "set — and it must be, or the build cannot find Next — Amplify rejects this."
    )
    assert "frontend" not in parsed, (
        "a top-level `frontend:` key alongside `applications:` is the single-app form "
        "leaking back in"
    )


def test_the_app_root_matches_the_directory_next_actually_lives_in():
    """`appRoot: web`, and `baseDirectory: .next` rather than `web/.next`.

    Paths are relative to `appRoot` in this form. The `web/` prefix belongs only to the
    single-app form, which runs from the repository root — and a prefixed path here
    produces an artifact directory Amplify cannot find, after a build that succeeded.
    """
    assert spec.parsed_app_root() == "web", spec.parsed_app_root()
    assert (REPO_ROOT / "web" / "package.json").is_file(), (
        "appRoot names a directory with no package.json in it"
    )

    app = yaml.safe_load(spec.build_spec())["applications"][0]
    base = app["frontend"]["artifacts"]["baseDirectory"]

    assert base == ".next", (
        f"baseDirectory is {base!r}. In the applications form the working directory is "
        f"already appRoot, so a `web/` prefix points at web/web/.next — and `out/` fails "
        f"with `cannot find required-server-files.json` because next export was removed."
    )


def test_the_build_runs_the_gates_before_it_builds():
    """A deploy that skips the gates can ship a broken authorization gate.

    Ordering, not presence: a `npm run build` that precedes the checks would produce an
    artifact before anything had a chance to refuse it.
    """
    commands = yaml.safe_load(spec.build_spec())["applications"][0]["frontend"]["phases"]["build"]["commands"]
    joined = "\n".join(commands)

    for gate in ("typecheck", "lint", "test"):
        assert f"npm run {gate}" in joined, f"the build does not run `npm run {gate}`"

    build_at = next(i for i, c in enumerate(commands) if c.strip() == "npm run build")
    for gate in ("typecheck", "lint", "test"):
        gate_at = next(i for i, c in enumerate(commands) if f"npm run {gate}" in c)
        assert gate_at < build_at, f"`npm run {gate}` runs AFTER the build"


# ── the variables, which the build no longer enforces ─────────────────────────

def test_the_buildspec_supplies_exactly_lane_ps_runtime_list():
    """The handoff between two lanes, and the build stopped checking it.

    Lane P measured `next build` at exit 0 with every variable unset, so nothing fails if
    this list is wrong — the app simply refuses every session at runtime and the symptom
    is "sign-in broke". This assertion is the only place the list is checked.
    """
    written = frozenset(spec.runtime_variables())

    assert written == EXPECTED_RUNTIME_VARIABLES, (
        f"the buildspec writes {sorted(written)}; Lane P's runtime list is "
        f"{sorted(EXPECTED_RUNTIME_VARIABLES)}. Missing: "
        f"{sorted(EXPECTED_RUNTIME_VARIABLES - written)}. Unexpected: "
        f"{sorted(written - EXPECTED_RUNTIME_VARIABLES)}."
    )


def test_no_variable_carries_the_reserved_aws_prefix():
    """Amplify rejects any variable starting with `AWS`, `AWS_REGION` included."""
    offenders = [v for v in spec.runtime_variables() if v.startswith(spec.RESERVED_PREFIX)]

    assert not offenders, f"Amplify refuses variables starting with AWS: {offenders}"


@pytest.mark.parametrize("bad", ["GRACE_TABLE_NAME ", " COGNITO_ISSUER", "A B"])
def test_a_key_with_whitespace_is_refused(bad):
    """Two keys entered by hand in the reference carried whitespace and read as ABSENT.

    `GRACE_TABLE_NAME  ` and `GRACE_ESCALATION _INDEX` — an exact-key lookup finds
    neither, and a value-trimming helper cannot trim a malformed KEY. So the failure
    presents as "the variable is not set" for a variable plainly visible in the console.
    """
    # `RuntimeError`, matching the code. Worth a note: this repository's one stated rule
    # about the choice is `RetrievalBoundaryViolation`'s — "a RuntimeError rather than a
    # ValueError: this is not a bad value, it is a call that must not exist" — and a
    # malformed key IS a bad value by that reading. The type is not load-bearing here
    # (nothing catches it; both spellings crash a provisioning script equally loudly), so
    # the test follows the code rather than churning it. Recorded so the next person to
    # touch this sees the inconsistency was noticed rather than missed.
    with pytest.raises(RuntimeError, match="whitespace|key"):
        spec.refuse_reserved_and_malformed({bad: "value"})


def test_a_reserved_key_is_refused_before_an_api_call():
    """Caught here rather than by Amplify, so the message names the rule."""
    with pytest.raises(RuntimeError, match="AWS"):
        spec.refuse_reserved_and_malformed({"AWS_REGION": "us-east-1"})


# ── the full-replace trap, which cost the reference a build ───────────────────

def test_merging_preserves_keys_this_module_does_not_own():
    """`update_app(environmentVariables=...)` is a FULL REPLACE.

    The console writes its own keys into the same map, so a map built from a stale read
    DELETED `AMPLIFY_MONOREPO_APP_ROOT`, `AMPLIFY_DIFF_DEPLOY` and the Node-22 pin — and
    the next build died at clone time with `Cannot read 'next' version in package.json`,
    which reads like a packaging problem rather than a deleted variable.

    So a converge reads fresh and merges, preserving every `AMPLIFY_*` and
    `_LIVE_UPDATES` key it does not own.
    """
    existing = {
        "AMPLIFY_MONOREPO_APP_ROOT": "web",
        "AMPLIFY_DIFF_DEPLOY": "false",
        "_LIVE_UPDATES": "[]",
        "COGNITO_ISSUER": "stale",
    }

    merged = spec.merged_environment(
        existing,
        COGNITO_ISSUER="fresh", COGNITO_CLIENT_ID="c",
        COGNITO_DOMAIN="d", AUTH_URL="https://example.invalid",
    )

    assert merged["AMPLIFY_DIFF_DEPLOY"] == "false", (
        "a key this module does not own was dropped; the next build fails at clone time"
    )
    assert merged["_LIVE_UPDATES"] == "[]", "the Node pin was dropped"
    assert merged["COGNITO_ISSUER"] == "fresh", "an owned key was not updated"


def test_unowned_keys_reports_what_a_converge_is_carrying():
    """Named rather than silently carried, so a converge says what it preserved.

    A key nobody can enumerate is a key nobody reviews, and this map is the one whose
    accidental truncation broke the reference's build.
    """
    carried = spec.unowned_keys({
        "AMPLIFY_DIFF_DEPLOY": "false",
        "_LIVE_UPDATES": "[]",
        "COGNITO_ISSUER": "owned, so not reported",
        "SOMETHING_ELSE": "not an AMPLIFY key, so not preserved",
    })

    assert carried == {"AMPLIFY_DIFF_DEPLOY", "_LIVE_UPDATES"}, sorted(carried)
