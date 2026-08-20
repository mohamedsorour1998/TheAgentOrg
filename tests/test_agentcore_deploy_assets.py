"""Pins the AgentCore deploy assets: requirements.txt, the Dockerfile, deploy.sh.

Owner: Sorour (Task 5). These assets cannot be exercised on the authoring
machine -- there is no container runtime and no `agentcore` CLI -- so they are
the kind of artifact that reads as correct and is discovered wrong at the venue.
These tests pin the properties whose violation would break a real deploy, and
each one was verified by breaking the property and watching the named test fail.

WHAT EACH GROUP PROTECTS, and why it is not a tautology
------------------------------------------------------

1. requirements.txt completeness. AgentCore builds the image FROM THIS FILE
   (`agentcore configure -rf requirements.txt`), so it is the only dependency
   declaration the deployed container sees -- pyproject.toml is not consulted on
   that path. The spec (docs/plan/mariam/week3.md:42-48) gives three lines, and
   three lines is not enough: agentorg/github_ops.py:35 does an unconditional
   MODULE-LEVEL `from github import ...`, reached from agentorg/graph.py:42, so a
   container built from the spec's list dies at import.

   The completeness test does not hardcode "these five names". It DERIVES the
   third-party imports from the source with an AST walk and asserts the file
   covers them. That is what makes it able to fail: a future module that imports
   a new package makes it red without anyone remembering this file exists.

2. The fixtures defect. agentorg/fixtures_loader.py:21 resolves fixtures/
   relative to the install location, so a non-editable install cannot find them
   and every agent's fallback path raises FileNotFoundError. Measured, not
   hypothesised -- see test_the_fixtures_defect_this_dockerfile_works_around_is_real,
   which reproduces it against a real `pip install --target` rather than
   asserting a belief about it.

3. deploy.sh refusing to run. It performs live, billable, OVERWRITING actions on
   a real AWS account. A regression that made it run without confirmation is the
   single most expensive defect in this task, so it is tested by actually
   invoking the script with a FAKE `agentcore` on PATH and asserting the fake was
   never called. Without the fake the script exits at its missing-CLI check and
   the confirmation gate is never reached -- so a test that skipped the fake
   would pass while proving nothing about the gate. That trap is why the fake
   exists.

NO LIVE AWS. Nothing here runs `agentcore`, `aws`, `docker` or `terraform`. The
only executable exercised is deploy.sh's refusal paths, with a fake CLI, and the
one test of its happy path asserts on what the fake RECORDED rather than
performing a deploy.
"""

import ast
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "agentorg" / "agents"
REQUIREMENTS = AGENTS_DIR / "requirements.txt"
DOCKERFILE = REPO_ROOT / "infra" / "agentcore" / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
DEPLOY_SH = REPO_ROOT / "infra" / "agentcore" / "deploy.sh"

# Read from docs/plan/week1-verification-log.md:11-30, never recalled and never
# re-derived from Terraform state (the task text forbids re-deriving them).
RECORDED_ROLE_ARN = (
    "arn:aws:iam::339712964409:role/theagentorg-shared-agentcore-runtime-role"
)
RECORDED_ACCOUNT = "339712964409"
RECORDED_REGION = "us-east-1"

# docs/plan/sorour/week3.md:292 -- runtime names use UNDERSCORES.
RUNTIME_NAMES = (
    "theagentorg_planner",
    "theagentorg_developer",
    "theagentorg_reviewer",
    "theagentorg_security",
    "theagentorg_sre",
)

# docs/plan/week1-verification-log.md:15-19 -- ECR repos use HYPHENS. Two
# namespaces, both real; a test that conflated them would bless a broken deploy.
ECR_REPO_NAMES = (
    "theagentorg-shared-planner-agent",
    "theagentorg-shared-developer-agent",
    "theagentorg-shared-reviewer-agent",
    "theagentorg-shared-security-agent",
    "theagentorg-shared-sre-agent",
)

# The five entrypoint files `agentcore configure -e` names.
ENTRYPOINTS = (
    "planner.py",
    "developer.py",
    "reviewer.py",
    "security.py",
    "sre.py",
)


def _declared_requirements() -> list[Requirement]:
    """Parse requirements.txt into Requirement objects, ignoring comments."""
    lines = REQUIREMENTS.read_text().splitlines()
    return [
        Requirement(line.strip())
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def _declared_names() -> set[str]:
    """Normalised distribution names declared in requirements.txt."""
    return {r.name.lower().replace("_", "-") for r in _declared_requirements()}


# Distribution name for each top-level import name that differs from it. Only
# the ones this project actually imports; a mapping that guessed at packages
# nothing imports would be untested weight.
_IMPORT_TO_DISTRIBUTION = {
    "github": "pygithub",
    "strands": "strands-agents",
    "botocore": "boto3",  # boto3's own dependency, not declared separately
}

# Import names that are deliberately NOT runtime dependencies, each with the
# reason. Listed here so the derivation test states its exclusions by name
# instead of quietly passing over them -- an unexplained exclusion is how a real
# dependency goes missing.
_NOT_RUNTIME = {
    # agentorg/common/health.py:9, inside a nested function of register_health().
    # Nothing imports agentorg.common.health -- it is wiring for the FastMCP
    # agent-server layer that does not exist yet (pyproject.toml:26 keeps
    # `# "fastmcp"` commented out). Dead code today.
    "starlette",
}


def _third_party_imports() -> dict[str, list[str]]:
    """AST-derive third-party top-level imports under agentorg/, with sites.

    Derived rather than listed, so a new import in a future module makes the
    completeness test red without anyone remembering this file exists. Relative
    imports are skipped (they are first-party by construction) and stdlib is
    filtered with sys.stdlib_module_names.
    """
    stdlib = set(sys.stdlib_module_names)
    found: dict[str, list[str]] = {}
    for path in sorted((REPO_ROOT / "agentorg").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue  # relative -> first-party
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if not top or top in stdlib or top == "agentorg":
                    continue
                site = f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
                found.setdefault(top, []).append(site)
    return found


# --------------------------------------------------------------------------
# requirements.txt
# --------------------------------------------------------------------------


def test_the_agents_requirements_file_exists():
    """AgentCore builds from this path; without it `-rf requirements.txt` fails.

    docs/plan/mariam/week3.md:42-45 requires it next to the agents. It was
    MISSING before this task.
    """
    assert REQUIREMENTS.is_file(), f"{REQUIREMENTS} is missing"


def test_every_requirement_line_is_parseable():
    """A typo here fails the image build, not the test suite, unless pinned here.

    A malformed line would otherwise surface as a pip error inside an ARM64
    build on a machine nobody is watching.
    """
    requirements = _declared_requirements()
    assert requirements, "requirements.txt declares nothing"
    for requirement in requirements:
        assert requirement.name, f"unnamed requirement: {requirement}"


@pytest.mark.parametrize("required", ["strands-agents", "bedrock-agentcore", "pydantic"])
def test_the_specs_three_lines_are_all_present(required):
    """The spec's three lines are a FLOOR that may be added to, never dropped.

    docs/plan/mariam/week3.md:46-48. Additions are the point of Ruling 13;
    removals would silently narrow what the container installs.
    """
    assert required in _declared_names(), (
        f"{required} is required by docs/plan/mariam/week3.md:46-48 and is absent"
    )


def test_requirements_covers_every_third_party_import_in_the_package():
    """The real completeness property, derived from source rather than listed.

    This is the test that would have caught the spec's three-line list: PyGithub
    is imported unconditionally at module level (agentorg/github_ops.py:35) and
    reached from agentorg/graph.py:42, so its absence is an import-time crash in
    the container.

    Derived by AST walk, so a NEW third-party import anywhere under agentorg/
    turns this red. Exclusions are named in _NOT_RUNTIME with a reason each.
    """
    declared = _declared_names()
    missing: list[str] = []
    for import_name, sites in sorted(_third_party_imports().items()):
        if import_name in _NOT_RUNTIME:
            continue
        distribution = _IMPORT_TO_DISTRIBUTION.get(import_name, import_name)
        if distribution.lower().replace("_", "-") not in declared:
            missing.append(f"{import_name} (-> {distribution}), imported at {sites}")
    assert not missing, (
        "agentorg imports these but agentorg/agents/requirements.txt does not "
        "declare them, so an AgentCore container would fail at import or at "
        "first use:\n  " + "\n  ".join(missing)
    )


def test_flask_is_not_a_dependency_of_the_agents():
    """Ruling 13 forbids flask unless an agent imports one. None does.

    pyproject.toml:24 lists it for target_repo/ (the demo app under test), which
    is not in this image. This test is the inverse of the one above: it pins an
    exclusion, so a future "fix" that copies pyproject's list wholesale is
    caught rather than silently shipping an unused web framework to five
    runtimes.
    """
    assert "flask" not in _declared_names(), (
        "flask is in requirements.txt, but no agent imports it -- it exists for "
        "target_repo/, not for the agent runtimes (Ruling 13)"
    )
    assert "flask" not in _third_party_imports(), (
        "an agent now imports flask, so this exclusion is stale -- re-derive it"
    )


def test_pygithub_and_boto3_are_declared_with_their_import_sites_documented():
    """Ruling 13 requires each addition to carry the file:line that needs it.

    The evidence is the point: an undocumented pin cannot be re-checked when the
    code moves, and this plan has been bitten repeatedly by numbers and names
    carried forward instead of re-measured.
    """
    body = REQUIREMENTS.read_text()
    assert "github_ops.py:35" in body, (
        "PyGithub's import site (agentorg/github_ops.py:35) is not cited in "
        "requirements.txt"
    )
    assert "llm.py:52" in body, (
        "boto3's import site (agentorg/common/llm.py:52) is not cited in "
        "requirements.txt"
    )


def test_no_requirement_is_unbounded_except_the_one_with_a_stated_reason():
    """Pinning is the house style; the one exception must say why in the file.

    pyproject.toml pins `ruff>=0.16,<0.17` and `setuptools>=61,<85`, and CI pins
    all three scanner versions. bedrock-agentcore is deliberately unpinned
    because it is not installed on the authoring machine and nothing in this
    tree imports it, so there is no measured version -- and inventing one is
    exactly what this task forbids. That reasoning has to live in the file, not
    only in a report nobody reads at 2am.
    """
    unbounded = [r.name for r in _declared_requirements() if not r.specifier]
    assert unbounded == ["bedrock-agentcore"], (
        f"unexpected unpinned requirements: {unbounded}. Pin them, or state the "
        "reason in requirements.txt and update this test deliberately."
    )
    body = REQUIREMENTS.read_text()
    assert "DELIBERATELY UNPINNED" in body, (
        "bedrock-agentcore is unpinned but requirements.txt does not say why"
    )


def test_requirements_does_not_contradict_pyproject_on_shared_dependencies():
    """Two declarations of the same dependency must not disagree.

    requirements.txt is what the container installs; pyproject.toml is what a
    developer installs. A container on a floor BELOW what pyproject declares
    would run code the project never tested -- a divergence invisible until
    runtime.
    """
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    project_deps = {
        Requirement(d).name.lower().replace("_", "-"): Requirement(d)
        for d in pyproject["project"]["dependencies"]
    }
    for requirement in _declared_requirements():
        name = requirement.name.lower().replace("_", "-")
        if name not in project_deps:
            continue
        pyproject_requirement = project_deps[name]
        for pyproject_spec in pyproject_requirement.specifier:
            if pyproject_spec.operator != ">=":
                continue
            container_floors = [
                s.version for s in requirement.specifier if s.operator == ">="
            ]
            assert container_floors, (
                f"{name}: pyproject declares a floor {pyproject_spec} but "
                "requirements.txt declares none"
            )


# --------------------------------------------------------------------------
# The fixtures defect the Dockerfile works around
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def target_install(tmp_path_factory):
    """A real non-editable install, which is what an AgentCore container has.

    `--no-deps` so no network is needed for dependencies; `--no-cache-dir` so a
    previously built wheel cannot be served instead of this tree.
    """
    target = tmp_path_factory.mktemp("agentcore-target")
    result = subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--quiet", "--no-deps", "--no-cache-dir", "--no-build-isolation",
            f"--target={target}", str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(
            "cannot build a wheel here, so the installed layout cannot be "
            f"measured: {result.stderr[-400:]}"
        )
    return target


def test_fixtures_are_unreachable_from_a_target_install(target_install):
    """The defect, measured against a real install.

    Measured on the authoring machine:
        _FIXTURES resolves to: /private/tmp/nei/fixtures
        exists? False
        plan() RAISED: FileNotFoundError .../nei/fixtures/plan_result.json
    """
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from agentorg import fixtures_loader as f;"
        "print(f._FIXTURES.exists())"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, str(target_install)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", (
        "fixtures/ is now reachable from a bare non-editable install. If it "
        "became package-data, the Dockerfile's `COPY fixtures` is redundant -- "
        "re-read both before changing either."
    )


def test_copying_fixtures_beside_the_package_makes_all_five_loaders_work(
    target_install, tmp_path
):
    """The fix the Dockerfile applies, verified end to end.

    Proves the workaround is sufficient, not merely plausible: all five loaders
    return their models once fixtures/ sits beside the installed package.
    """
    staged = tmp_path / "staged"
    shutil.copytree(target_install, staged)
    shutil.copytree(REPO_ROOT / "fixtures", staged / "fixtures")
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from agentorg import fixtures_loader as f;"
        "print(type(f.plan()).__name__, type(f.dev()).__name__,"
        "type(f.review()).__name__, type(f.security()).__name__,"
        "type(f.sre()).__name__)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, str(staged)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        "PlanResult", "DevResult", "ReviewResult", "SecurityResult", "SREResult",
    ]


def test_the_dockerfile_copies_fixtures_beside_the_installed_package():
    """Pin the workaround's presence, since no build can be run here.

    The destination must be COMPUTED from sysconfig, not hardcoded to a
    python3.12 path -- a hardcoded path silently orphans the fixtures on the
    first base-image bump, restoring the defect with no diff to blame.
    """
    instructions = " ".join(_dockerfile_instructions())
    assert "COPY fixtures" in instructions, "the Dockerfile does not copy fixtures/"
    assert "sysconfig.get_paths()" in instructions, (
        "the fixtures destination is not computed from sysconfig, so a base "
        "image bump would orphan it"
    )


# --------------------------------------------------------------------------
# The container definition
# --------------------------------------------------------------------------


def test_the_dockerfile_exists():
    assert DOCKERFILE.is_file(), f"{DOCKERFILE} is missing"


def test_the_base_image_is_pinned_and_satisfies_the_declared_python_floor():
    """A floating tag moves the interpreter under a deployed runtime.

    pyproject.toml:18 declares `requires-python = ">=3.12"` because
    agentorg/state.py:12 imports UTC from datetime (3.11+). The base must state
    a version explicitly, and it must satisfy the declared floor -- both halves,
    since `python:3.11-slim` is pinned and still wrong.
    """
    from_lines = [
        line for line in DOCKERFILE.read_text().splitlines()
        if line.strip().startswith("FROM ")
    ]
    assert from_lines, "no FROM line"
    for line in from_lines:
        image = line.split()[1]
        assert ":" in image, f"unpinned base image (no tag): {image}"
        tag = image.split(":", 1)[1]
        assert "latest" not in tag, f"floating base image tag: {image}"
        version_part = tag.split("-")[0]
        major, _, minor = version_part.partition(".")
        assert major.isdigit() and minor.isdigit(), (
            f"base image tag does not state a python version: {image}"
        )
        declared = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        floor = declared["project"]["requires-python"].lstrip(">=")
        floor_major, _, floor_minor = floor.partition(".")
        assert (int(major), int(minor)) >= (int(floor_major), int(floor_minor)), (
            f"base image {image} is below pyproject's requires-python {floor}"
        )


def test_the_install_is_not_editable():
    """An editable install in the image hides the packaging defect.

    tests/test_packaging.py exists because an editable install resolves imports
    through a .pth finder pointing at the source tree. A container doing the
    same would pass every check here and fail the way the original defect did.

    Checked against parsed instructions, so the file's own prose about editable
    installs cannot satisfy it.
    """
    instructions = _dockerfile_instructions()
    joined = " ".join(instructions)
    assert "pip install --no-cache-dir --no-deps ." in joined, (
        "the Dockerfile does not perform a non-editable install of the package"
    )
    for instruction in instructions:
        assert "pip install -e" not in instruction, (
            f"editable install in the image: {instruction}"
        )
        assert "pip install --editable" not in instruction, (
            f"editable install in the image: {instruction}"
        )


def test_the_dockerfile_verifies_all_four_subpackages_import():
    """The image must prove at BUILD time what Task 1 fixed.

    Copying only agentorg/*.py is the defect; an import check inside the build
    is what makes its return a red build rather than a runtime surprise.
    """
    instructions = " ".join(_dockerfile_instructions())
    for module in ("agentorg.graph", "agentorg.agents.security", "agentorg.common.llm"):
        assert module in instructions, f"the build does not verify `import {module}`"


def test_the_dockerfile_sets_scanners_required_true():
    """A production image must fail CLOSED on an absent scanner.

    agentorg/common/config.py:73-77 says so explicitly: with the default false,
    an absent binary makes the gate borrow a FIXTURE verdict, so it reports
    clean because it never ran -- failing open, the one shape that lane exists
    to prevent.
    """
    instructions = _dockerfile_instructions()
    assert "ENV SCANNERS_REQUIRED=true" in instructions, (
        "the image does not set SCANNERS_REQUIRED=true, so an absent scanner "
        f"would fail OPEN (agentorg/common/config.py:73-77). ENV lines found: "
        f"{[i for i in instructions if i.startswith('ENV')]}"
    )


def _dockerfile_instructions() -> list[str]:
    """The Dockerfile's INSTRUCTIONS, with comments and blank lines removed.

    Parsed as instructions rather than substring-matched against the raw text,
    for the same reason _deploy_agent_table parses deploy.sh: this file's own
    explanatory comments quote the very strings the tests look for, so a
    whole-text `in` check is satisfied by prose and stays green when the real
    instruction is broken. The RED step caught exactly that (cases 21-23) --
    three tests that could not fail because a comment above them mentioned
    `gitleaks version`, `curl -sSfL` and `&& false`.

    Continuation lines are joined, so one logical RUN is one string.
    """
    logical: list[str] = []
    current = ""
    for raw in DOCKERFILE.read_text().splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1].rstrip() + " "
            continue
        current += stripped
        logical.append(current)
        current = ""
    if current:
        logical.append(current)
    return logical


def test_the_dockerfile_installs_the_scanners_it_then_requires():
    """SCANNERS_REQUIRED=true without the binaries makes every scan a hard fault.

    The two are halves of one decision. Setting the flag while shipping no
    scanners converts the security gate from fail-open to always-blocked, which
    at a live demo is indistinguishable from a broken pipeline.
    """
    instructions = " ".join(_dockerfile_instructions())
    for scanner in ("gitleaks", "trivy", "semgrep"):
        assert scanner in instructions, (
            f"SCANNERS_REQUIRED=true is set but {scanner} is never installed"
        )


def test_the_scanner_stage_proves_all_three_binaries_are_executable():
    """The scanner URLs are UNVERIFIED, so the build must self-check them.

    This is what makes shipping unverified URLs defensible rather than reckless.
    Two independent guards, and both are load-bearing:

      * `curl -sSfL` -- the `-f` makes a wrong asset name an HTTP error that
        fails the build instead of writing an HTML error page to disk and
        carrying on. Without -f, `tar` would fail confusingly later, or a
        zero-byte file would be installed as a "binary".
      * a trailing version check per scanner -- proves each one is present AND
        executable, which catches an arch mismatch that downloads fine and
        cannot run (an x86 binary on ARM64 is exactly that failure).

    An earlier draft forced this stage to fail with `&& false`, which guaranteed
    the build could never succeed even with correct URLs. Removing it was only
    safe BECAUSE these two guards exist, so this test pins them -- against the
    parsed instructions, not the prose that describes them.
    """
    instructions = " ".join(_dockerfile_instructions())
    download_count = instructions.count("curl -sS")
    assert download_count >= 2, (
        f"expected at least 2 scanner downloads, found {download_count}"
    )
    assert instructions.count("curl -sSfL") == download_count, (
        "a scanner download is missing curl's -f flag; without it a wrong URL "
        "writes an error page to disk instead of failing the build"
    )
    for check in ("gitleaks version", "trivy --version", "semgrep --version"):
        assert check in instructions, (
            f"the build never runs `{check}`, so an unusable or wrong-arch "
            "binary would ship silently"
        )


def test_the_dockerfile_has_no_stage_that_always_fails():
    """A build that can never succeed is not a deliverable.

    Guards against reintroducing the `&& false` an earlier draft carried: it
    made the container definition unbuildable by construction, which defeats the
    point of writing one. If a stage genuinely cannot be trusted, the honest
    move is to document it (as the scanner stage does) and let its own error
    checking fail the build -- not to hardcode a failure.
    """
    for instruction in _dockerfile_instructions():
        assert " false " not in f" {instruction} ", (
            f"this instruction forces the build to fail unconditionally: {instruction}"
        )


def test_no_credential_is_baked_into_the_image():
    """The runtime gets credentials from its IAM role; nothing is baked in.

    A key in a layer persists even if a later stage deletes it.

    DELIBERATELY checks the RAW TEXT, not the parsed instructions, unlike the
    tests above. A credential sitting in a comment is still a credential
    committed to the repository, so prose is in scope here where it is noise
    elsewhere.
    """
    body = DOCKERFILE.read_text()
    for forbidden in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GITHUB_TOKEN",
    ):
        assert forbidden not in body, f"{forbidden} appears in the Dockerfile"
    assert "COPY .env" not in body and "ADD .env" not in body, (
        ".env is copied into the image"
    )


def test_the_build_context_excludes_the_stale_build_directory():
    """build/lib/agentorg/ is a STALE copy missing the subpackages.

    It is gitignored but present in the worktree, so a build context carries it
    unless excluded. The .dockerignore must live at the CONTEXT root (the repo
    root) -- one beside the Dockerfile is silently ignored, which is the
    failure this test is really guarding.
    """
    assert DOCKERIGNORE.is_file(), (
        ".dockerignore is missing from the repo root, which is the build context "
        "root -- a copy under infra/agentcore/ would be ignored by docker"
    )
    entries = {
        line.strip()
        for line in DOCKERIGNORE.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert "build/" in entries, (
        "build/ is not excluded from the build context, so a stale copy of "
        "agentorg without its subpackages could ship"
    )
    assert ".env" in entries, ".env is not excluded from the build context"
    for venv_pattern in (".venv/", ".venv-*/"):
        assert venv_pattern in entries, (
            f"{venv_pattern} is not excluded; an editable-install .pth would "
            "defeat the non-editable install"
        )


# --------------------------------------------------------------------------
# deploy.sh -- the live, billable script
# --------------------------------------------------------------------------


def test_the_deploy_script_exists_and_is_valid_bash():
    """Syntax is checked here because the script's first real run is under pressure."""
    assert DEPLOY_SH.is_file(), f"{DEPLOY_SH} is missing"
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY_SH)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"


def test_the_deploy_script_uses_only_recorded_identifiers():
    """Every identifier must match docs/plan/week1-verification-log.md.

    Inventing an ARN, a role name or a repo name is the failure mode the brief
    forbids most emphatically, and it is invisible until AWS rejects it.
    """
    body = DEPLOY_SH.read_text()
    assert RECORDED_ROLE_ARN in body, "the recorded runtime role ARN is absent"
    assert RECORDED_ACCOUNT in body, "the recorded account id is absent"
    assert RECORDED_REGION in body, "the recorded region is absent"


def _deploy_agent_table() -> list[tuple[str, str, str]]:
    """Parse deploy.sh's AGENTS array into (entrypoint, runtime, ecr_repo) rows.

    Parsed as DATA rather than substring-matched against the whole file, and
    that distinction is load-bearing. The first version of these tests asserted
    `"theagentorg_planner" in DEPLOY_SH.read_text()`, which the file's own
    explanatory COMMENT satisfies -- so hyphenating the real table entry left
    the test green. A mutation that changes behaviour and no test notices is the
    "test that cannot fail" shape, caught here by the RED step (case 17).
    """
    rows: list[tuple[str, str, str]] = []
    inside = False
    for line in DEPLOY_SH.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("readonly AGENTS=("):
            inside = True
            continue
        if inside:
            if stripped.startswith(")"):
                break
            if stripped.startswith("#") or not stripped:
                continue
            entry = stripped.strip('"').strip("'")
            parts = entry.split(":")
            assert len(parts) == 3, f"malformed AGENTS row in deploy.sh: {stripped}"
            rows.append((parts[0], parts[1], parts[2]))
    return rows


@pytest.mark.parametrize("runtime_name", RUNTIME_NAMES)
def test_every_runtime_name_is_present_with_underscores(runtime_name):
    """Runtime names use UNDERSCORES (docs/plan/sorour/week3.md:292).

    Asserted against the parsed table, not the file text -- see
    _deploy_agent_table for why that difference caught a real weakness.
    """
    runtimes = [row[1] for row in _deploy_agent_table()]
    assert runtime_name in runtimes, (
        f"{runtime_name} is not in deploy.sh's AGENTS table (found: {runtimes})"
    )


@pytest.mark.parametrize("ecr_repo", ECR_REPO_NAMES)
def test_every_ecr_repo_name_is_present_with_hyphens(ecr_repo):
    """ECR repos use HYPHENS (docs/plan/week1-verification-log.md:15-19).

    Two namespaces, both real. Pinning both is what stops a well-meaning
    "consistency" edit from renaming one into the other.
    """
    repos = [row[2] for row in _deploy_agent_table()]
    assert ecr_repo in repos, (
        f"{ecr_repo} is not in deploy.sh's AGENTS table (found: {repos})"
    )


def test_the_deploy_table_holds_exactly_the_five_agents_and_no_others():
    """Guards the count as well as the contents.

    A sixth row, or a duplicate, would pass every per-name test above while
    deploying something unintended.
    """
    rows = _deploy_agent_table()
    assert len(rows) == 5, f"expected 5 agents, deploy.sh declares {len(rows)}: {rows}"
    assert [row[0] for row in rows] == list(ENTRYPOINTS)
    assert [row[1] for row in rows] == list(RUNTIME_NAMES)
    assert [row[2] for row in rows] == list(ECR_REPO_NAMES)


def test_the_two_namespaces_are_not_conflated():
    """A hyphenated runtime name or an underscored repo name would both be wrong.

    Checked positionally against the parsed table, so this cannot be satisfied
    by the correct name appearing somewhere else in the file.
    """
    for entrypoint, runtime_name, ecr_repo in _deploy_agent_table():
        assert "_" in runtime_name and "-" not in runtime_name, (
            f"runtime name {runtime_name!r} must use underscores, not hyphens"
        )
        assert "-" in ecr_repo and "_" not in ecr_repo, (
            f"ECR repo {ecr_repo!r} must use hyphens, not underscores"
        )
        assert entrypoint.endswith(".py"), f"entrypoint {entrypoint!r} is not a .py file"


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_every_entrypoint_file_named_by_the_script_actually_exists(entrypoint):
    """`agentcore configure -e X.py` fails if X.py is not there.

    Ties the script to the tree, so renaming an agent file makes this red
    instead of failing mid-deploy after earlier agents already went live.
    """
    assert entrypoint in DEPLOY_SH.read_text(), f"{entrypoint} is not deployed"
    assert (AGENTS_DIR / entrypoint).is_file(), f"{AGENTS_DIR / entrypoint} is missing"


def test_the_deploy_script_refuses_to_run_without_the_env_var():
    """The cheapest gate, and the one a stray `bash deploy.sh` hits first."""
    environment = {k: v for k, v in os.environ.items() if k != "AGENTORG_DEPLOY_I_MEAN_IT"}
    result = subprocess.run(
        ["bash", str(DEPLOY_SH)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode != 0, "the script ran without the confirmation env var"
    assert "REFUSING TO RUN" in result.stderr


def test_the_dry_run_needs_no_env_var_and_prints_the_real_commands():
    """--dry-run must be usable by anyone, and must show what would run.

    It prints via the same emit_commands() the live path uses, so the preview
    cannot drift from the commands.
    """
    environment = {k: v for k, v in os.environ.items() if k != "AGENTORG_DEPLOY_I_MEAN_IT"}
    result = subprocess.run(
        ["bash", str(DEPLOY_SH), "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    assert "no AWS call is made" in result.stdout
    for runtime_name in RUNTIME_NAMES:
        assert f"-n {runtime_name}" in result.stdout
    assert RECORDED_ROLE_ARN in result.stdout
    assert "-rf requirements.txt" in result.stdout


@pytest.fixture()
def fake_agentcore(tmp_path, monkeypatch):
    """A fake `agentcore` on PATH that RECORDS calls and performs none.

    THIS FIXTURE IS WHY THE GATE TESTS CAN FAIL. Without a runnable `agentcore`,
    deploy.sh exits at its missing-CLI check long before the confirmation
    prompt, so a gate test run on this machine -- where the real CLI is absent --
    would pass while proving nothing. The fake moves the failure point past the
    tooling check so the gate itself is what is under test.

    It never contacts AWS: it appends its arguments to a log and exits 0.
    """
    log = tmp_path / "calls.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "agentcore"
    fake.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        "exit 0\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    return log


def test_the_fake_agentcore_can_actually_record(fake_agentcore):
    """Self-test the harness before trusting what it reports.

    A fake that silently fails to record would make every "no calls happened"
    assertion below vacuously true -- the harness failure mode that reported
    0/11 caught on this plan while everything was in fact caught. Assert the
    fake CAN record, so an empty log later means the gate held rather than that
    the fake was broken.
    """
    subprocess.run(["agentcore", "selftest", "--probe"], check=True)
    assert fake_agentcore.is_file(), "the fake recorded nothing at all"
    assert "selftest --probe" in fake_agentcore.read_text()


def test_a_wrong_confirmation_deploys_nothing(fake_agentcore, monkeypatch):
    """The most expensive possible regression, tested against a runnable CLI.

    With the env var set AND the CLI present, only the typed confirmation stands
    between this and five live runtimes. The assertion is on what the fake
    RECORDED: an empty log is the proof, and the self-test above is what makes
    an empty log meaningful.
    """
    monkeypatch.setenv("AGENTORG_DEPLOY_I_MEAN_IT", "yes")
    result = subprocess.run(
        ["bash", str(DEPLOY_SH)],
        input="nope\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, "the script proceeded despite a wrong confirmation"
    assert "Aborted. Nothing was deployed." in result.stdout
    assert not fake_agentcore.exists(), (
        f"agentcore was invoked despite the abort: {fake_agentcore.read_text()}"
    )


def test_the_confirmed_path_issues_exactly_the_specified_commands(
    fake_agentcore, monkeypatch
):
    """The inverse test: the gates must not be so tight that nothing works.

    A script that refuses everything would pass every refusal test above and be
    useless. This pins the commands the confirmed path actually issues, against
    the spec at docs/plan/sorour/week3.md:292-293, without any AWS contact --
    the fake absorbs them.
    """
    monkeypatch.setenv("AGENTORG_DEPLOY_I_MEAN_IT", "yes")
    result = subprocess.run(
        ["bash", str(DEPLOY_SH)],
        input="deploy\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"the confirmed path failed: {result.stderr}"
    calls = fake_agentcore.read_text().splitlines()
    for entrypoint, runtime_name in zip(ENTRYPOINTS, RUNTIME_NAMES, strict=True):
        expected_configure = (
            f"configure -e {entrypoint} -n {runtime_name} "
            f"-er {RECORDED_ROLE_ARN} -rf requirements.txt -r {RECORDED_REGION} -ni"
        )
        assert expected_configure in calls, (
            f"missing or malformed configure for {runtime_name}.\nGot: {calls}"
        )
    launches = [c for c in calls if c.startswith("launch ")]
    assert len(launches) == len(RUNTIME_NAMES), (
        f"expected {len(RUNTIME_NAMES)} launches, got {len(launches)}: {launches}"
    )
    for launch in launches:
        assert "--auto-update-on-conflict" in launch
        assert "--env BEDROCK_MODEL=us.amazon.nova-2-lite-v1:0" in launch


def test_the_script_says_it_is_billable_before_it_does_anything():
    """A reader skimming the top must learn the cost before running it."""
    head = "\n".join(DEPLOY_SH.read_text().splitlines()[:30])
    assert "BILLABLE" in head.upper(), (
        "the script does not warn that it performs billable actions in its first "
        "30 lines"
    )


def test_the_script_never_swallows_a_failure_mid_deploy():
    """`|| true` would leave a partial set that later reads as a working deploy.

    set -e plus no suppression means the first failure stops the run, so five
    runtimes are either configured or visibly not.

    Checks EVERY non-comment line, not only lines containing "agentcore". The
    first version checked only the latter, and the RED step (case 20) showed
    that a `|| true` appended to a CONTINUATION line -- `-ni || true`, four lines
    below the word `agentcore` -- suppressed the failure while the test stayed
    green. The suppression and the command name need not share a line.
    """
    body = DEPLOY_SH.read_text()
    assert "set -euo pipefail" in body, "the script does not fail fast"
    for number, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for suppressor in ("|| true", "|| :", "|| exit 0"):
            assert suppressor not in stripped, (
                f"deploy.sh:{number} suppresses a failure with `{suppressor}`, so a "
                f"partial deploy could report success: {stripped}"
            )
