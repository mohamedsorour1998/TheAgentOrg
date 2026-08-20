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

  * Whole-file run, declaration broken AND `-S` dropped: `7 failed, 24 passed`.
    The subpackage and data-file tests read the filesystem directly, so they do
    not care about `-S`, and `test_the_editable_finder_is_not_what_resolves_these_imports`
    fires too. CI runs whole files, so CI would catch that pairing.
  * Whole-file run, declaration CORRECT and `-S` dropped: `1 failed, 30 passed`.
    The single failure is `test_the_isolation_flag_is_actually_set`, and it is
    the ONLY thing that notices -- every import still resolves from the temp
    install, because the finder is a fallback and the packaged tree satisfies
    them on its own.

That second measurement is why that test exists. Before it was added the same
mutation was a fully green run, so a cleanup dropping the `-S` looked harmless
forever and only became a hole when a later packaging change landed. It asserts
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

A skip survives for exactly one case: no index reachable AND no build backend
installed, i.e. the environment cannot build any package at all and so cannot say
anything about this declaration either way. Verified with a deps directory
holding pytest and packaging but no setuptools: `17 passed, 14 skipped` -- the 11
integration tests skip, three predicate rows that need setuptools present defer
via `_requires_installed`, and nothing fails.

Getting that boundary right took two goes, because pip reports a bad declaration
and a bare environment with the SAME words -- `BackendUnavailable: Cannot import
'<backend>'`. Two defects hid in the gap:

  * `build-backend = "setuptools.build_metaa"` (a typo, fatal to every container
    build) gave `11 skipped`, RC=0 -- with the network UP, and still `11 skipped`
    with the broken `packages = ["agentorg"]` declaration alongside it.
  * A `requires` whose marker excludes this interpreter gave `11 passed` while
    the real isolated build died with `BackendUnavailable`.

Both are decided structurally now, never from pip's message text:
`_declared_backend_is_importable` resolves the declared module with `find_spec`,
and `_local_backend_satisfies_declared_requires` evaluates markers and URL pins
as well as version specifiers. Each of those checks is scoped to the case where
the backend distribution is actually INSTALLED here -- otherwise a bare
environment would be reported as a packaging defect, which is the same
conflation in the opposite direction (measured: it turned the legitimate skip
into `11 errors` before that scoping was added).

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

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest
from packaging.requirements import InvalidRequirement, Requirement

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
    """Whether pip failed because it could not obtain a build backend.

    Necessary but NOT sufficient for a skip, and deliberately the LAST question
    asked. pip reports the same words for two opposite causes -- nothing
    installed (environment limitation) versus a declaration naming a backend
    nothing can supply (packaging defect) -- so this predicate cannot decide
    alone. `_declared_build_system_is_coherent` rules out the defect first.

    Both alternatives are needed and neither is redundant:
    `BackendUnavailable` is the exception type pip raises when a declared backend
    will not import, and "Could not find a version" / "No matching distribution"
    is what an isolated build prints when it cannot even provision `requires`.
    Kept anchored to those specific phrasings rather than a bare "Cannot import",
    which would match any import error anywhere in arbitrary pip output --
    including one raised by the project's own code during a build.
    """
    return (
        "BackendUnavailable" in output
        or "Cannot import 'setuptools.build_meta'" in output
        or "No matching distribution found for setuptools" in output
        or "Could not find a version that satisfies the requirement setuptools" in output
    )


def _build_system_table() -> dict:
    """The `[build-system]` table as declared, or an empty dict if absent."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return pyproject.get("build-system", {})


def _declared_backend() -> str | None:
    """The `build-system.build-backend` string, or None if the table omits it."""
    return _build_system_table().get("build-backend")


def _declared_requires() -> list[str]:
    """The `build-system.requires` list as declared, or empty if absent."""
    return _build_system_table().get("requires", [])


def _normalised_parts(name: str) -> tuple[str, ...]:
    """Split a distribution or module name into comparable pieces.

    PEP 503 treats runs of `-`, `_` and `.` as equivalent and folds case, so
    `poetry-core` and `poetry.core` are the same name written two ways. Reducing
    both sides to a tuple of lowercase pieces is what lets `_requires_can_supply`
    below match a distribution against the module path it provides.
    """
    return tuple(piece for piece in re.split(r"[-_.]+", name.strip().lower()) if piece)


def _requires_can_supply(backend: str, requires: list[str]) -> bool:
    """Whether some entry in `requires` could plausibly provide `backend`.

    This is the COHERENCE question, and it is the only one of these checks that
    needs neither the network nor any local state -- which is exactly why it is
    the right instrument. `build-backend = "flit_core.buildapi"` under
    `requires = ["setuptools>=61,<85"]` is broken on its face: no isolated build
    could ever satisfy that pairing, on any machine, however well stocked.

    Root-presence cannot answer this. It reports the same thing for "a backend
    that exists on PyPI but is absent here" and "a backend that exists nowhere at
    all", so it separates a typo'd submodule from everything else rather than
    separating a defect from an environment. Measured, all three of
    `nosuchpkg.api`, `flit_core.buildapi` and `poetry.core.masonry.api` gave
    `11 skipped` while the real isolated build died with `BackendUnavailable`.

    THIS IS A HEURISTIC, not an exact test, and the imprecision is one-sided.
    Matching is by normalised name prefix: a distribution is taken to supply a
    backend when its name equals the leading segments of the backend's module
    path, so `setuptools` supplies `setuptools.build_meta`, `poetry-core`
    supplies `poetry.core.masonry.api` (PEP 503 folds `-`, `_` and `.`), and
    `wheel` supplies neither.

    What it CANNOT do, stated plainly rather than implied away:

      * It never opens the distribution to see which modules it actually ships.
        A `requires` entry named `foo` will vouch for
        `build-backend = "foo.bar.baz"` even when that distribution contains no
        such module. Nothing here would catch that.
      * It cannot see a backend re-exported under an unrelated name, so a
        legitimate but unconventional pairing would read as incoherent. No such
        pairing is known in this project's dependencies.
      * It says nothing about VERSIONS. `_local_backend_satisfies_declared_requires`
        owns that question.

    The bias is deliberate: false-negative (accepting an incoherent pairing)
    rather than false-positive (rejecting a valid one), so this predicate can
    only ever fail to accuse -- never accuse wrongly. Anything it lets through
    still has to survive the real build, which is what reports the typo'd
    submodule case. Making it exact would mean importing or unpacking candidate
    distributions, which reintroduces exactly the local-state and network
    dependence that makes this check trustworthy on a bare machine.

    An empty `requires` is not incoherent -- pip then falls back to implicit
    setuptools, which is the documented deleted-table boundary, so it returns
    True and leaves that case alone.
    """
    if not requires:
        return True

    module = backend.split(":", 1)[0]
    backend_parts = _normalised_parts(module)

    for raw in requires:
        try:
            name = Requirement(raw).name
        except InvalidRequirement:
            # Unparseable entries are out of scope (pip rejects them too); do not
            # let one turn into a false accusation of incoherence.
            return True
        dist_parts = _normalised_parts(name)
        if backend_parts[: len(dist_parts)] == dist_parts:
            return True

    return False


def _declared_build_system_is_coherent() -> bool:
    """Whether `[build-system]` could build ANYWHERE, independent of this machine.

    Asked BEFORE any decision to skip. Two failure modes pip reports with the
    same words -- `BackendUnavailable: Cannot import '<backend>'` -- have to be
    told apart here, because one is a legitimate skip and the other is a defect
    that breaks every container build:

      * The environment simply has no build backend installed. Nothing about the
        declaration is wrong, so the fixture skips.
      * The declaration names a backend nothing in `requires` can ever provide.
        No container could build it. That must FAIL.

    Coherence is what distinguishes them, and it keeps holding on a bare machine:
    `setuptools.build_meta` under `requires = ["setuptools..."]` stays coherent
    with nothing installed at all, so the backend-less case still skips.

    TWO questions are asked, because neither alone is sufficient and each covers
    the other's blind spot. Measured on the final tree, which is how this was caught:
    coherence ALONE let `setuptools.build_metaa` skip (`20 passed, 11 skipped`),
    because `setuptools` genuinely does supply the `setuptools.*` namespace --
    prefix matching cannot see a typo below the distribution name.

      1. COHERENCE (`_requires_can_supply`): could this pairing build anywhere?
         Catches a backend nothing in `requires` provides -- `nosuchpkg.api`,
         `flit_core.buildapi` -- and keeps holding on a bare machine, so the
         backend-less case still skips.
      2. LOCAL RESOLUTION: given that the supplying distribution IS installed
         here, does the exact declared module resolve? Catches the typo. Scoped
         to "the distribution is present", because asking it unscoped is what
         made a bare machine look like a defect in an earlier round.

    The scoping in (2) is the whole reason both can coexist. If the supplying
    distribution is absent, this says nothing and defers to the skip.
    """
    backend = _declared_backend()
    if backend is None:
        # No `build-backend` key: pip falls back to setuptools legacy. There is
        # no declared name to be incoherent with.
        return True

    if not isinstance(backend, str):
        # Wrong type is out of scope for this predicate; the generic failure path
        # reports it. Do not claim incoherence on something unparseable.
        return True

    requires = _declared_requires()

    # (1) Could this pairing ever build, on any machine?
    if not _requires_can_supply(backend, requires):
        return False

    # (2) The supplying distribution is declared. If it is also INSTALLED here,
    # the exact module must resolve -- otherwise the declaration names something
    # that distribution does not contain. If it is absent, stay silent: that is
    # the bare-machine case and the skip owns it.
    module = backend.split(":", 1)[0]
    root = module.split(".", 1)[0]
    try:
        if importlib.util.find_spec(root) is None:
            return True
    except (ImportError, ValueError):
        return True

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


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

    Three details that each hid a defect, all now covered:

      * MARKERS. A requirement whose environment marker excludes this
        interpreter will never be installed by an isolated build, so the backend
        will be ABSENT there -- it is not "satisfied", it is inapplicable.
        Evaluating only `.specifier` missed that: measured,
        `requires = ["setuptools>=61,<85; python_version < \"3.12\""]` -- the
        wrong side of this project's own `requires-python = ">=3.12"`, so it can
        never apply -- gave `11 passed` while the real isolated build died with
        `BackendUnavailable: Cannot import 'setuptools.build_meta'`. A marker
        that evaluates False therefore means "this pin cannot vouch for the
        retry", i.e. False.
      * DIRECT REFERENCES. `setuptools @ https://...` pins an exact artifact.
        Whatever is installed locally is not knowably that artifact, so a
        direct reference can never justify the retry either.
      * Extras (`setuptools[core]>=61`) and case (`Setuptools`) need no special
        handling: `Requirement` strips extras from `.name`, and
        `importlib.metadata.version` normalises the name.
    """
    backend = _declared_backend()

    for raw in _declared_requires():
        requirement = Requirement(raw)

        # A marker that excludes this environment means the isolated build would
        # not install this requirement at all, so the backend would be ABSENT
        # there. Not satisfied -- inapplicable.
        if requirement.marker is not None and not requirement.marker.evaluate():
            return False

        # A URL pin names an exact artifact. Whatever is installed locally is not
        # knowably that artifact, so it cannot justify the retry.
        if requirement.url is not None:
            return False

        try:
            installed = version(requirement.name)
        except PackageNotFoundError:
            # Absent locally. Which of the two meanings this has depends on
            # WHICH distribution is missing, and conflating them is what the
            # previous two rounds each did in one direction:
            #
            #   * It is the one supplying the declared backend. Then this is the
            #     bare-machine case -- nothing is wrong with the declaration, the
            #     environment simply cannot build -- and the skip legitimately
            #     owns it. Returning False here reported a packaging defect on a
            #     clean machine (measured: 11 errors instead of 11 skipped).
            #   * It is any OTHER entry. Then an isolated build would have to
            #     resolve it and could not, so the declaration is unbuildable and
            #     must fail. Returning True here let `requires = ["wheel"]` pass
            #     11/11 while the real build died with BackendUnavailable.
            if backend is not None and isinstance(backend, str):
                return _requires_can_supply(backend, [raw])
            return True

        if not requirement.specifier.contains(installed, prereleases=True):
            return False
    return True


# ---------------------------------------------------------------------------
# Unit tests for the two skip/fail predicates.
#
# These exist because manual RED evidence missed the same class of bug TWICE, in
# opposite directions: one round broke the bare-machine case, the next fixed that
# and broke the unsatisfiable-pin case. Every hand mutation used `setuptools`,
# which is installed here, so no hand mutation could ever exercise the
# absent-distribution arm that both bugs lived in.
#
# Both predicates are pure functions of a TOML string, so these point REPO_ROOT at
# a tmp_path and assert the whole table with NO pip invocation -- milliseconds,
# and no network.
#
# `hatchling` and `flit_core` are the "absent" distributions. Pinned by
# test_the_absent_distributions_these_cases_rely_on_are_absent, so if either ever
# gets installed the affected rows fail loudly instead of silently inverting --
# see the note on that constant for the ordering bug this actually caught.
# ---------------------------------------------------------------------------

# NOT `wheel`. Importing setuptools injects a VENDORED wheel into the metadata
# path, so `wheel` is absent only until something resolves `setuptools.*` -- after
# which `importlib.metadata.version("wheel")` returns 0.46.3. Measured:
#
#     before:                                    ABSENT
#     after find_spec("setuptools.build_meta"):   0.46.3
#
# That made these rows ORDER-DEPENDENT: the requires table passed alone (11
# passed) and failed once the coherence table ran first, because its very first
# row -- the VALID declaration -- resolves setuptools and conjures wheel. The
# guard below caught it by name, which is what it is for.
#
# hatchling and flit_core stay absent across that import, so they are safe
# stand-ins for "a distribution this environment does not have".
ABSENT_DISTRIBUTIONS = ("hatchling", "flit_core")

# The mirror image: rows that assert a verdict only reachable when setuptools IS
# installed. Measured -- running this file on a machine with no setuptools turned
# two "unsatisfiable pin" rows green-to-red, because an absent distribution takes
# the defer-to-skip arm instead of the version-comparison arm. That is correct
# behaviour reaching a row that assumed otherwise, so those rows are skipped
# rather than allowed to fail.
PRESENT_DISTRIBUTIONS = ("setuptools",)


def _requires_installed(*names: str) -> None:
    """Skip the calling test unless every named distribution is installed here."""
    for name in names:
        try:
            version(name)
        except PackageNotFoundError:
            pytest.skip(
                f"{name} is not installed, so this row cannot exercise the "
                "version-comparison path it is written for"
            )


def _write_pyproject(tmp_path, monkeypatch, body: str) -> None:
    """Point the predicates at a throwaway pyproject.toml containing `body`.

    Patches the LIVE module object, resolved from `sys.modules` by this module's
    own `__name__`, rather than by a dotted string. Two traps, both hit here:

      * There is no `tests/__init__.py`, so pytest imports this file as top-level
        `test_packaging`. A string target of "tests.test_packaging.REPO_ROOT"
        imports a SECOND, separate module object and patches that one, leaving
        the predicates reading the real repository. That fails silently in the
        comfortable direction -- 8 "expected False" rows passed against the real
        declaration, testing nothing.
      * `monkeypatch.setitem(globals(), ...)` mutates the module dict but does
        not reliably restore it, which leaked a tmp_path into later tests: one row
        failed in a whole-file run while passing when selected alone.

    `setattr` on the module object is restored properly, so each row gets a clean
    REPO_ROOT and the integration tests below still see the real one.
    """
    (tmp_path / "pyproject.toml").write_text(body)
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)


def test_the_absent_distributions_these_cases_rely_on_are_absent():
    """Guard on the fixtures below: they only mean anything if these are missing.

    Several rows assert a verdict that depends on `wheel` / `flit_core` NOT being
    installed. If a future dev extra adds one, those rows would still pass while
    testing the opposite situation. Fail here instead, by name.
    """
    for name in ABSENT_DISTRIBUTIONS:
        try:
            found = version(name)
        except PackageNotFoundError:
            continue
        pytest.fail(
            f"{name} {found} is installed, but the predicate tests below use it "
            "as a stand-in for an ABSENT distribution. Those cases are no longer "
            "testing what they claim -- pick a different absent distribution.",
            pytrace=False,
        )


# (declaration, expected coherent?, why, distributions that must be installed)
COHERENCE_CASES = (
    (
        'requires = ["setuptools>=61,<85"]\nbuild-backend = "setuptools.build_meta"',
        True,
        "the real declaration",
        (),
    ),
    (
        'requires = ["setuptools>=61,<85"]\nbuild-backend = "setuptools.build_metaa"',
        False,
        ("a typo'd submodule: COHERENT (setuptools does supply setuptools.*) but "
         "the exact module does not resolve, and setuptools is installed here so "
         "that is knowable. Coherence alone let this skip -- measured, "
         "`20 passed, 11 skipped` -- which is why local resolution is asked too"),
        PRESENT_DISTRIBUTIONS,
    ),
    (
        'requires = ["setuptools>=61,<85"]\nbuild-backend = "nosuchpkg.api"',
        False,
        "nothing in requires can ever supply nosuchpkg",
        (),
    ),
    (
        'requires = ["setuptools>=61,<85"]\nbuild-backend = "flit_core.buildapi"',
        False,
        "a real third-party backend, but not one this requires provides",
        (),
    ),
    (
        'requires = ["poetry-core>=1.0"]\nbuild-backend = "poetry.core.masonry.api"',
        True,
        "PEP 503 name normalisation: poetry-core supplies poetry.core.*",
        (),
    ),
    (
        'requires = ["setuptools>=61,<85", "hatchling"]\nbuild-backend = "setuptools.build_meta"',
        True,
        "an extra entry does not make a coherent pairing incoherent",
        (),
    ),
    (
        'requires = []\nbuild-backend = "setuptools.build_meta"',
        True,
        "empty requires is the implicit-setuptools boundary, left alone",
        (),
    ),
    (
        'requires = ["setuptools>=61,<85"]',
        True,
        "no build-backend key: nothing to be incoherent with",
        (),
    ),
)


@pytest.mark.parametrize(("body", "expected", "why", "needs_installed"), COHERENCE_CASES)
def test_build_system_coherence(tmp_path, monkeypatch, body, expected, why, needs_installed):
    """`[build-system]` coherence, judged without network or local state."""
    _requires_installed(*needs_installed)
    _write_pyproject(tmp_path, monkeypatch, f"[build-system]\n{body}\n")

    assert _declared_build_system_is_coherent() is expected, why


# (declaration, expected satisfied?, why, distributions that must be installed)
REQUIRES_CASES = (
    (
        'requires = ["setuptools>=61,<85"]\nbuild-backend = "setuptools.build_meta"',
        True,
        "installed setuptools satisfies the real pin",
        (),
    ),
    (
        'requires = ["setuptools>=999"]\nbuild-backend = "setuptools.build_meta"',
        False,
        "unsatisfiable pin on a PRESENT distribution",
        PRESENT_DISTRIBUTIONS,
    ),
    (
        ('requires = ["setuptools>=61,<85; python_version < \'3.12\'"]\n'
         'build-backend = "setuptools.build_meta"'),
        False,
        "marker excludes this interpreter, so an isolated build installs nothing",
        (),
    ),
    (
        ('requires = ["setuptools>=61,<85; python_version >= \'3.12\'"]\n'
         'build-backend = "setuptools.build_meta"'),
        True,
        "an applicable marker must NOT be rejected",
        (),
    ),
    (
        'requires = ["hatchling"]\nbuild-backend = "setuptools.build_meta"',
        False,
        ("absent distribution that does NOT supply the backend -- the regression "
         "that passed 11/11 for a whole round"),
        (),
    ),
    (
        ('requires = ["setuptools>=61,<85", "hatchling>=99"]\n'
         'build-backend = "setuptools.build_meta"'),
        False,
        "multi-entry: one good, one absent-and-unsatisfiable",
        PRESENT_DISTRIBUTIONS,
    ),
    (
        ('requires = ["hatchling @ https://example.invalid/x.whl"]\n'
         'build-backend = "setuptools.build_meta"'),
        False,
        "direct URL on an ABSENT distribution -- previously short-circuited to True",
        (),
    ),
    (
        ('requires = ["setuptools @ https://example.invalid/s.whl"]\n'
         'build-backend = "setuptools.build_meta"'),
        False,
        "direct URL on a PRESENT distribution",
        (),
    ),
    (
        'requires = ["setuptools[core]>=61"]\nbuild-backend = "setuptools.build_meta"',
        True,
        "extras are stripped from the requirement name",
        (),
    ),
    (
        'requires = ["SETUPTOOLS>=61"]\nbuild-backend = "setuptools.build_meta"',
        True,
        "distribution names are case-insensitive",
        (),
    ),
    (
        'requires = ["flit_core>=3"]\nbuild-backend = "flit_core.buildapi"',
        True,
        ("THE BARE-MACHINE CASE: the absent distribution is the one supplying "
         "the declared backend, so this must defer to the skip, not accuse the "
         "declaration"),
        (),
    ),
)


@pytest.mark.parametrize(("body", "expected", "why", "needs_installed"), REQUIRES_CASES)
def test_requires_satisfaction(tmp_path, monkeypatch, body, expected, why, needs_installed):
    """Whether the local backend can vouch for a `--no-build-isolation` retry."""
    _requires_installed(*needs_installed)
    _write_pyproject(tmp_path, monkeypatch, f"[build-system]\n{body}\n")

    assert _local_backend_satisfies_declared_requires() is expected, why


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
            # The ONLY defensible skip: the environment cannot obtain a build
            # backend at all, so it cannot say anything about this declaration
            # either way. Both halves are required.
            #
            # The second half is load-bearing and was missing. pip says
            # `BackendUnavailable: Cannot import '<backend>'` both when nothing
            # is installed AND when `build-backend` names a module that does not
            # exist -- and the latter is a defect that must fail. Asking
            # find_spec, rather than reading pip's words, tells them apart. See
            # _declared_backend_is_importable.
            if not _declared_build_system_is_coherent():
                pytest.fail(
                    "no build could ever succeed with this [build-system]: "
                    f"build-backend is {_declared_backend()!r} but requires is "
                    f"{_declared_requires()!r}, and nothing in requires can "
                    "supply that backend. This is a packaging defect, not an "
                    "environment limitation: every container build would fail, "
                    f"on any machine.\n{combined}",
                    pytrace=False,
                )

            if _is_backend_unobtainable(fallback.stdout + fallback.stderr):
                pytest.skip(
                    "cannot build a wheel: no index reachable and no build "
                    "backend importable in this environment, so the declaration "
                    f"cannot be exercised either way.\n{combined}"
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
    `7 failed, 24 passed`, and this test is one of the seven, reporting "resolved
    ... to the SOURCE WORKTREE".

    It does NOT fire when the `-S` is dropped while the declaration is correct
    (whole-file run: `1 failed, 30 passed`, the one failure being that test
    itself), because the editable finder is a FALLBACK --
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
