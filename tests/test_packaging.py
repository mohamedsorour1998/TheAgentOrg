"""Pins what a NON-EDITABLE install of this package actually ships. Owner: Sorour.

Everything up to now has run from source or from the editable install in
`.venv-sorour`, and both of those put the whole worktree on the import path. An
AgentCore container does not: it `pip install`s the project, so only what the
build backend was told to ship exists at runtime. That gap hid a real defect.

THE DEFECT THIS CLOSES
----------------------

`pyproject.toml` declared:

    [tool.setuptools]
    packages = ["agentorg"]

That is an EXACT list, not a prefix, so setuptools shipped `agentorg/*.py` and
nothing below it. Measured with `pip install --no-deps --target=` at the parent
commit, the installed `agentorg/` contained exactly:

    __init__.py  fixtures_loader.py  gates.py  gates_cli.py
    github_ops.py  graph.py  log.py  state.py

`agents/`, `common/` and `security/` were all absent, so the very first import
died:

    File ".../agentorg/graph.py", line 42, in <module>
        from . import gates, github_ops, log
    File ".../agentorg/github_ops.py", line 35, in <module>
        from .agents.security import _one_line
    ModuleNotFoundError: No module named 'agentorg.agents'

Every agent image would have built green and failed on import at runtime.

WHY THIS TEST HAS TO RUN A SUBPROCESS WITH `-S`
-----------------------------------------------

This is the part that makes the test real, and it is easy to get wrong in a way
that reads as coverage while proving nothing.

`.venv-sorour` holds `__editable__.theagentorg-0.1.0.pth`, which installs the
PEP-660 meta-path finder in `__editable___theagentorg_0_1_0_finder.py`. Its
MAPPING is `{'agentorg': '<worktree>/agentorg'}`. The hook that matters is a
FALLBACK -- `if not paths and fullname in MAPPING` -- so it supplies `agentorg`
from the SOURCE WORKTREE whenever nothing else on the path could.

That is exactly the case a broken declaration produces, and it is not
theoretical. Against `packages = ["agentorg"]`, with the subpackages genuinely
absent from the target directory:

    PYTHONPATH=<target> .venv-sorour/bin/python -c "import agentorg.agents.security"
    -> SPURIOUS SUCCESS from <worktree>/agentorg/agents/security.py

So an in-process import, or any subprocess that loads the venv's site-packages,
passes against the very defect this file exists to catch. Measured on a SELECTED
node -- `pytest "tests/test_packaging.py::test_the_packaged_install_can_be_imported"`
with the declaration reverted and the `-S` dropped -- that is 3 passed against a
tree containing no subpackages at all.

`-S` is what defeats it: it skips `site`, so the `.pth` never executes and the
finder is never installed. The subpackages then have to be really present in the
target for the import to resolve.

Two measurements bound how much protection there is if someone deletes the `-S`,
and the distinction matters because an earlier version of this docstring got it
wrong in the pessimistic direction:

  * Whole-file run, declaration broken AND `-S` dropped: `6 failed, 3 passed`.
    The subpackage and data-file tests read the filesystem directly, so they do
    not care about `-S`, and `test_the_editable_finder_is_not_what_resolves_these_imports`
    fires too. CI runs whole files, so CI would catch that pairing.
  * Whole-file run, declaration CORRECT and `-S` dropped: `10 passed`. Nothing
    notices, because the finder is only a fallback and the temp install satisfies
    every import on its own.

The second line is the real risk: a cleanup that drops the `-S` looks harmless
forever, and only becomes a hole when a later packaging change lands. That is
what `test_the_isolation_flag_is_actually_set` closes -- it asserts
`sys.flags.no_site` from inside the subprocess, so it fails on `-S` removal
regardless of the declaration's state.

`-S` also means the venv's site-packages is not on the path, so pydantic and the
rest are gone too. Hence the explicit two-entry PYTHONPATH: the temp target
first, the venv's already-installed dependencies second. That also keeps the
install itself `--no-deps`, so this test needs no network for dependencies.

WHY THIS FILE MUST NOT SKIP WHEN THE NETWORK IS DOWN
----------------------------------------------------

An isolated PEP 517 build provisions its backend from an index, so on a machine
with no network and a cold pip cache the build cannot start. The first version of
this file skipped in that case, reasoning that a missing wheelhouse is not a
packaging defect. That reasoning was wrong, and measurably so:

    PIP_NO_INDEX=1 pytest tests/test_packaging.py -q   ->  9 skipped, RC=0

RC=0 held even with the declaration reverted to the broken `packages =
["agentorg"]`. A skip that survives the defect the file exists to catch is not a
safety net -- CI reports green on a container-breaking bug.

Two changes fix that. `pyproject.toml` now declares `[build-system]` explicitly
instead of leaning on pip's implicit `setuptools>=40.8.0`, and the fixture RETRIES
with `--no-build-isolation`, which uses the setuptools already installed here and
so needs no index at all. Same command with both in place:

    PIP_NO_INDEX=1 pytest tests/test_packaging.py -q   ->  10 passed

and with the declaration reverted as well:

    PIP_NO_INDEX=1 pytest tests/test_packaging.py -q   ->  10 failed

which is the point: offline, the verdict now tracks the declaration.

The retry is GATED, because an ungated one hides a different defect --
`--no-build-isolation` ignores `[build-system] requires` entirely, so it will
happily build against a pin no container could satisfy. See
`_local_backend_satisfies_declared_requires`. `--no-deps` throughout means this
project's own runtime dependencies are never fetched.

A skip survives for exactly one case: no index reachable AND no setuptools
importable, i.e. the environment cannot build any package at all and so cannot
say anything about this declaration either way.

WHY IT BUILDS FROM A COPY AND PASSES `--no-cache-dir`
-----------------------------------------------------

Two stale-input traps, both of which can make this test lie in either direction.
setuptools reuses `build/lib/`, and this worktree had a `build/lib/agentorg/`
holding an old copy that was itself missing the subpackages. And pip caches the
built wheel: `pip cache list` showed `theagentorg-0.1.0-py3-none-any.whl` twice.
Either one can serve output built from a declaration other than the one on disk.

So the fixture copies `pyproject.toml` plus `agentorg/` into a fresh tmp_path and
builds THAT. A directory that was created seconds ago cannot hold stale build
output, which removes the trap structurally rather than by remembering to clean
up. It also keeps the working tree clean -- building in place leaves `build/` and
`theagentorg.egg-info/` behind. `--no-cache-dir` closes the wheel-cache half.
"""

import os
import shutil
import subprocess
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest
from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parent.parent

# The three imports that a container actually performs. `agentorg.graph` is the
# one that failed, and it fails transitively through github_ops -> agents; the
# other two name the remaining subpackages directly so a partial regression
# cannot hide behind graph.py's import chain.
REQUIRED_IMPORTS = (
    "agentorg.graph",
    "agentorg.agents.security",
    "agentorg.common.llm",
)

REQUIRED_SUBPACKAGES = ("agents", "common", "security")

# Data files whose absence agentorg/security/semgrep_tool.py reports as a broken
# install rather than an absent scanner ("its rules file is missing from the
# installed package"). gitleaks_tool.py passes its CONFIG_PATH straight to
# --config. Both are found via Path(__file__), so they must sit beside the
# module, which means they only exist if package-data ships them.
REQUIRED_DATA_FILES = (
    "agentorg/security/gitleaks.toml",
    "agentorg/security/semgrep_rules.yml",
)

# Dependencies live here already. `-S` drops the venv's site-packages, so the
# subprocess needs this handed to it explicitly.
VENV_SITE_PACKAGES = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"


def _pip_install(source: Path, target: Path, *, isolated: bool) -> subprocess.CompletedProcess:
    """Run one `pip install` of `source` into `target`."""
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--quiet",
        "--no-deps",       # never fetch this project's runtime deps
        "--no-cache-dir",  # never serve a previously built wheel
    ]
    if not isolated:
        # Use the setuptools already in this venv instead of provisioning a
        # fresh one. This is the offline path -- see the fixture below.
        command.append("--no-build-isolation")
    command += [f"--target={target}", str(source)]

    return subprocess.run(command, capture_output=True, text=True, check=False)


def _is_backend_unobtainable(output: str) -> bool:
    """True only when pip could not IMPORT a build backend at all.

    Deliberately narrow: it must not match a build that ran and failed. The
    signal is `--no-build-isolation` being unable to import the declared
    backend, i.e. it is genuinely not installed here:

        BackendUnavailable: Cannot import 'setuptools.build_meta'
    """
    return "BackendUnavailable" in output or "Cannot import 'setuptools.build_meta'" in output


def _local_backend_satisfies_declared_requires() -> bool:
    """Whether the INSTALLED backend satisfies `[build-system] requires`.

    This gates the `--no-build-isolation` retry, and it is the difference
    between a useful fallback and a fallback that hides defects.

    `--no-build-isolation` does not read `requires` at all -- it just imports
    whatever backend is installed. So retrying unconditionally makes an
    UNSATISFIABLE pin build happily: measured, `requires = ["setuptools>=999"]`
    installed fine and all 10 tests passed, even though an isolated build (what
    a container does) cannot resolve that pin and every image would fail. That
    is a genuine packaging defect being masked by the test's own safety net.

    So the retry is only allowed when the local backend is a version the
    declared pin would actually have accepted. If it is not, the isolated
    build's failure is the honest answer and it propagates.
    """
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    requires = pyproject.get("build-system", {}).get("requires", [])

    for raw in requires:
        requirement = Requirement(raw)
        try:
            installed = version(requirement.name)
        except PackageNotFoundError:
            return False
        if not requirement.specifier.contains(installed, prereleases=True):
            return False
    return True


@pytest.fixture(scope="module")
def installed_package(tmp_path_factory):
    """`pip install` this project into a throwaway dir and hand back the path.

    Module-scoped because the build is the slow part and none of the tests below
    mutate the result.
    """
    target = tmp_path_factory.mktemp("pkgtest")

    # Build from a COPY, not from the worktree. Two reasons, both in the module
    # docstring: setuptools reuses a stale build/lib/ if one is there, and
    # building in place would litter the working tree with build/ and
    # *.egg-info. Copying only the declaration plus the package means the copy
    # cannot contain stale output, so no cleanup step has to be remembered.
    source = tmp_path_factory.mktemp("pkgsrc")
    shutil.copy2(REPO_ROOT / "pyproject.toml", source / "pyproject.toml")
    shutil.copytree(
        REPO_ROOT / "agentorg",
        source / "agentorg",
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    # Attempt 1: a normal isolated PEP 517 build, exactly what a container does.
    result = _pip_install(source, target, isolated=True)

    if result.returncode != 0:
        # Attempt 2: the same build using the setuptools already installed in
        # this venv. Isolated builds provision the backend from an index, so
        # attempt 1 fails on a machine with no network and a cold pip cache --
        # and THAT is what used to make this whole file skip, which meant CI
        # reported green on the container-breaking defect it exists to catch.
        # Retrying without isolation removes the network from the equation.
        if not _local_backend_satisfies_declared_requires():
            # The declared `requires` cannot be met by what is installed here,
            # so a --no-build-isolation retry would build with a backend the
            # declaration does not actually allow -- passing a build that a
            # container could never perform. Report the isolated failure.
            pytest.fail(
                "pip could not build the package, and [build-system] requires "
                "in pyproject.toml is not satisfied by the installed backend, "
                "so this cannot be retried without isolation. A container build "
                f"would fail the same way.\n{result.stdout}{result.stderr}",
                pytrace=False,
            )

        fallback = _pip_install(source, target, isolated=False)
        if fallback.returncode != 0:
            combined = (
                f"isolated build:\n{result.stdout}{result.stderr}\n"
                f"--no-build-isolation retry:\n{fallback.stdout}{fallback.stderr}"
            )
            if _is_backend_unobtainable(fallback.stdout + fallback.stderr):
                # No network AND no local setuptools. The environment cannot
                # build any package at all, so it cannot say anything about
                # THIS package's declaration. Fix: pip install setuptools.
                pytest.skip(
                    "cannot build a wheel: no index reachable and no setuptools "
                    "importable in this environment, so the declaration cannot "
                    f"be exercised either way.\n{combined}"
                )
            pytest.fail(f"pip install failed:\n{combined}", pytrace=False)

    return target


def _import_in_clean_subprocess(target: Path, module: str) -> subprocess.CompletedProcess:
    """Import `module` from `target` with the editable finder defeated.

    `-S` skips site processing, so `__editable__.theagentorg-0.1.0.pth` never
    runs and cannot redirect `agentorg` to the source worktree. See the module
    docstring -- without this the test passes against the very defect it exists
    to catch.

    Prints TWO lines, because the second is what keeps `-S` honest:

      1. the resolved `__file__` of the imported module
      2. `sys.flags.no_site`, which CPython sets to 1 under `-S` and 0 without it

    Reporting the flag from inside the subprocess is what lets
    `test_the_isolation_flag_is_actually_set` fail the moment the `-S` is
    deleted, without any test having to read its own source.
    """
    probe = (
        f"import sys; import {module}; "
        f"print({module}.__file__); print(sys.flags.no_site)"
    )
    return subprocess.run(
        [sys.executable, "-S", "-c", probe],
        cwd=target,
        env={
            "PYTHONPATH": os.pathsep.join([str(target), str(VENV_SITE_PACKAGES)]),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_isolation_flag_is_actually_set(installed_package):
    """Fail if the `-S` is ever removed from the import subprocess.

    `-S` is the single line that stops the editable finder from answering these
    imports out of the source worktree. Losing it does not turn the suite red on
    its own -- the finder is a fallback, so with a CORRECT declaration everything
    still resolves from the temp install -- which makes it exactly the kind of
    line a future cleanup deletes as redundant. The regression only surfaces
    later, paired with a packaging change, as a test that cannot fail.

    CPython sets `sys.flags.no_site` to 1 under `-S` and 0 without it, and
    `_import_in_clean_subprocess` reports it on its second output line. Asserting
    on that closes the gap directly, with no introspection of this file's own
    source.
    """
    result = _import_in_clean_subprocess(installed_package, "agentorg.common.llm")
    assert result.returncode == 0, (
        f"import failed, so the isolation flag cannot be read:\n{result.stderr}"
    )

    no_site = result.stdout.splitlines()[1].strip()

    assert no_site == "1", (
        "the import subprocess ran WITHOUT -S (sys.flags.no_site == "
        f"{no_site!r}), so site processing was active and "
        "__editable__.theagentorg-0.1.0.pth could resolve `agentorg` from the "
        "source worktree. Restore the -S: it is what makes these tests capable "
        "of failing."
    )


def test_the_editable_finder_is_not_what_resolves_these_imports(installed_package):
    """Guard on the instrument: assert these imports resolve to the TEMP INSTALL.

    Asserts these imports resolve inside the temp install, so it catches the
    packaged tree being bypassed entirely -- a `sys.path` order mistake, a stray
    `cwd`, or an import that silently reads the worktree. It fails with both
    paths printed.

    On the specific question of a deleted `-S`, this guard fires whenever the
    finder actually ends up answering the import, which is the case when the
    declaration is ALSO broken: whole-file run with both mutations gives
    `6 failed, 3 passed`, and this test is one of the six, reporting "resolved
    ... to the SOURCE WORKTREE".

    It does NOT fire when the `-S` is dropped while the declaration is correct
    (whole-file run: `10 passed`), because the editable finder is a FALLBACK --
    its hook is `if not paths and fullname in MAPPING`, so with the subpackages
    present in the temp target the PYTHONPATH entry wins and this assertion sees
    the temp install either way. `test_the_isolation_flag_is_actually_set` is
    what covers that case, by reading `sys.flags.no_site` directly.
    """
    result = _import_in_clean_subprocess(installed_package, "agentorg.agents.security")
    assert result.returncode == 0, (
        f"import failed, so the instrument cannot be checked:\n{result.stderr}"
    )

    resolved = Path(result.stdout.splitlines()[0].strip()).resolve()
    worktree_copy = (REPO_ROOT / "agentorg" / "agents" / "security.py").resolve()

    assert resolved != worktree_copy, (
        "this test resolved agentorg.agents.security to the SOURCE WORKTREE, so "
        "it is measuring the editable install and not the packaged one. The -S "
        "isolation is broken."
    )
    assert resolved.is_relative_to(installed_package.resolve()), (
        f"expected the import to resolve inside {installed_package}, got {resolved}"
    )


@pytest.mark.parametrize("subpackage", REQUIRED_SUBPACKAGES)
def test_a_non_editable_install_ships_every_subpackage(installed_package, subpackage):
    """`packages = ["agentorg"]` shipped only the top level. Pin that it doesn't."""
    shipped = installed_package / "agentorg" / subpackage

    assert shipped.is_dir(), (
        f"agentorg/{subpackage}/ is missing from a non-editable install. This is "
        "the container-build defect: the package declaration in pyproject.toml "
        "is not shipping subpackages.\n"
        f"what did ship: {sorted(p.name for p in (installed_package / 'agentorg').iterdir())}"
    )
    assert (shipped / "__init__.py").is_file(), (
        f"agentorg/{subpackage}/ shipped without its __init__.py"
    )


def test_the_install_ships_agentorg_and_nothing_else(installed_package):
    """Nothing but `agentorg` and its dist-info should land in the target.

    `include = ["agentorg*"]` is a prefix pattern, so the risk in the other
    direction is over-shipping: a bare `find` with no include, or a widened
    pattern, sweeps in `tests`, `scripts`, `infra`, `target_repo` and `docs` as
    top-level importable packages.

    Honest about the limit: this fixture builds from a copy holding only
    `pyproject.toml` and `agentorg/`, so a mutation to `include = ["*"]` has
    nothing extra to find and stays green. That hermetic copy is what closes the
    stale-build/lib trap, so it is deliberately kept. This assertion therefore
    catches accidental data-file or module sprawl inside the built wheel, not
    every possible widening of the pattern.
    """
    shipped = sorted(p.name for p in installed_package.iterdir())
    unexpected = [
        name
        for name in shipped
        if name != "agentorg" and not name.endswith(".dist-info")
    ]

    assert not unexpected, (
        f"a non-editable install put unexpected entries at the top level: {unexpected}. "
        "Only agentorg/ and its .dist-info should be there."
    )


@pytest.mark.parametrize("module", REQUIRED_IMPORTS)
def test_the_packaged_install_can_be_imported(installed_package, module):
    """The runtime check: what a container does on its first line of work."""
    result = _import_in_clean_subprocess(installed_package, module)

    assert result.returncode == 0, (
        f"`import {module}` failed against a non-editable install. A container "
        f"built from this declaration would fail at startup.\n{result.stderr}"
    )


@pytest.mark.parametrize("data_file", REQUIRED_DATA_FILES)
def test_the_scanner_config_files_ship_beside_their_modules(installed_package, data_file):
    """Non-.py files need package-data; without it semgrep reports a broken install.

    semgrep_tool.py resolves its rules with `Path(__file__).with_name(...)` and
    treats a miss as a FAULT, not as an absent scanner -- so if these do not ship
    the security gate misreports on a machine that HAS semgrep.
    """
    shipped = installed_package / data_file

    assert shipped.is_file(), (
        f"{data_file} is missing from a non-editable install. It is loaded "
        "relative to its module, so package-data has to ship it."
    )
    assert shipped.stat().st_size > 0, f"{data_file} shipped empty"
