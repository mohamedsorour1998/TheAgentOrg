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
passes against the very defect this file exists to catch. Measured directly:
revert the declaration AND drop the `-S`, and the three import tests below pass
3/3 against a tree containing no subpackages at all.

`-S` is what defeats it: it skips `site`, so the `.pth` never executes and the
finder is never installed. The subpackages then have to be really present in the
target for the import to resolve. Note that the suite stays GREEN if you remove
the `-S` while the declaration is correct -- the guard test explains why -- so
this is a line that has to be defended by reading, not by watching CI.

`-S` also means the venv's site-packages is not on the path, so pydantic and the
rest are gone too. Hence the explicit two-entry PYTHONPATH: the temp target
first, the venv's already-installed dependencies second. That also keeps the
install itself `--no-deps`, so this test needs no network for dependencies.

WHAT NEEDS NETWORK, AND WHY THAT IS NOT A `--no-index` TEST
-----------------------------------------------------------

`pyproject.toml` declares no `[build-system]`, so pip falls back to a PEP 517
build that requires `setuptools>=40.8.0` in an isolated env. `setuptools` is NOT
installed in `.venv-sorour`, so pip fetches it (or serves it from pip's HTTP
cache). Measured with `--no-index`:

    ERROR: Could not find a version that satisfies the requirement
    setuptools>=40.8.0 (from versions: none)

So this test can need the network on a cold pip cache, and is skipped rather
than failed when the build cannot get its backend -- a missing wheelhouse is not
a packaging defect. `--no-deps` still means it never fetches this project's own
runtime dependencies.

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
from pathlib import Path

import pytest

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

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-deps",       # never fetch this project's runtime deps
            "--no-cache-dir",  # never serve a previously built wheel
            f"--target={target}",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        combined = result.stdout + result.stderr
        # A PEP 517 build needs setuptools from the network on a cold cache.
        # That is an environment limitation, not a packaging defect.
        if "setuptools" in combined and (
            "No matching distribution" in combined
            or "Could not find a version" in combined
        ):
            pytest.skip(
                "cannot build a wheel: pip could not obtain the setuptools "
                f"build backend (needs network on a cold cache).\n{combined}"
            )
        pytest.fail(f"pip install failed:\n{combined}", pytrace=False)

    return target


def _import_in_clean_subprocess(target: Path, module: str) -> subprocess.CompletedProcess:
    """Import `module` from `target` with the editable finder defeated.

    `-S` skips site processing, so `__editable__.theagentorg-0.1.0.pth` never
    runs and cannot redirect `agentorg` to the source worktree. See the module
    docstring -- without this the test passes against the very defect it exists
    to catch.
    """
    return subprocess.run(
        [sys.executable, "-S", "-c", f"import {module}; print({module}.__file__)"],
        cwd=target,
        env={
            "PYTHONPATH": os.pathsep.join([str(target), str(VENV_SITE_PACKAGES)]),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_editable_finder_is_not_what_resolves_these_imports(installed_package):
    """Guard on the instrument: assert these imports resolve to the TEMP INSTALL.

    Read the scope of this guard carefully, because it is narrower than it looks
    and the difference was measured, not assumed.

    It DOES catch the case where the packaged tree is bypassed entirely -- a
    `sys.path` order mistake, a stray `cwd`, or an import that silently reads the
    worktree. It fails with both paths printed.

    It does NOT, on its own, catch the removal of `-S`. Measured: delete the
    `-S`, keep the fixed declaration, and all nine tests here still pass. The
    reason is that the editable finder is a FALLBACK, not an override -- its hook
    is `if not paths and fullname in MAPPING`, so it only supplies `agentorg`
    when nothing else did. With the subpackages present in the temp target, the
    PYTHONPATH entry wins and this assertion sees the temp install either way.

    What actually goes wrong without `-S` is the pairing with a BROKEN
    declaration, and that combination was measured too: revert the declaration
    AND drop the `-S`, and `test_the_packaged_install_can_be_imported` passes 3/3
    against a tree with no subpackages in it at all. That is the silent-coverage
    failure this file exists to prevent, and `-S` is the only thing preventing
    it. So `-S` is load-bearing and must not be removed to "simplify" the
    subprocess call, even though the suite stays green when you do.
    """
    result = _import_in_clean_subprocess(installed_package, "agentorg.agents.security")
    assert result.returncode == 0, (
        f"import failed, so the instrument cannot be checked:\n{result.stderr}"
    )

    resolved = Path(result.stdout.strip()).resolve()
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
