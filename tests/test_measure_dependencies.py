"""The dependency measurement, and the two ways it could lie.

OWNER: the integrator. Covers `scripts/measure_dependencies.py`, which answers judge
requirement 3 ("external dependency") with a number rather than a paragraph.

WHY THESE TESTS AND NOT OTHERS
==============================
The script replaced four grep counts in `docs/final/01-specification.md` §5 that reproduced
under no scope at all. Its value is entirely in one distinction -- MODULE-LEVEL (hard) vs
function-local (deferred) vendor imports -- so these tests exercise that distinction over
SYNTHETIC modules whose answer is known by construction, never over `agentorg/`.

Measuring the real package here would be the mistake the script itself was written to avoid:
the assertion would encode today's coupling, so it would fail when a lane legitimately
changed an import and pass while the classifier itself broke. `test_the_real_package_is_
measurable` is the one exception, and it deliberately asserts only that the measurement
RUNS and finds something -- not what it finds.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "measure_dependencies.py"


def _load():
    """Load the script as a module.

    It lives in `scripts/`, which is not a package, so a plain import cannot reach it.
    Same technique `tests/test_scanner_resilience.py` uses on `config.py`.
    """
    spec = importlib.util.spec_from_file_location("measure_dependencies", SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mdep():
    return _load()


# ── the distinction the whole script exists to make ───────────────────────────

def test_a_module_level_import_is_reported_hard(mdep, tmp_path):
    """The `github_ops.py:41` shape: unconditional, at the top of the file.

    This is the one that killed a container image -- CLAUDE.md records that the spec's
    three-line requirements.txt built an image dying at import, because this import runs
    whether or not the code path is taken.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "hard.py").write_text("from github import Github\n\ndef f():\n    return Github\n")

    rows, total = mdep.measure(pkg)

    assert total == 1
    assert len(rows) == 1, "the module imports a vendor and must appear"
    path, hard, deferred = rows[0]
    assert path == "hard.py", "the path must be relative to the package, not absolute"
    assert hard == {"github"}, f"a top-level import must be hard, got {hard}"
    assert deferred == set()


def test_a_function_local_import_is_reported_deferred(mdep, tmp_path):
    """The `llm.py` shape: the module loads without boto3; only one path needs it."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "soft.py").write_text("def call():\n    import boto3\n    return boto3\n")

    rows, _ = mdep.measure(pkg)

    _, hard, deferred = rows[0]
    assert hard == set(), f"an import inside a def is not module level, got {hard}"
    assert deferred == {"boto3"}


def test_a_try_guarded_import_at_the_top_is_still_hard(mdep, tmp_path):
    """`try: import boto3 / except ImportError:` runs at import time.

    Nested, but not deferred. Getting this wrong in the lenient direction would let a
    module claim it loads without a vendor when it does not.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "guarded.py").write_text(
        "try:\n    import boto3\nexcept ImportError:\n    boto3 = None\n"
    )

    rows, _ = mdep.measure(pkg)
    _, hard, deferred = rows[0]

    assert hard == {"boto3"}, "a top-level try/except import runs when the module loads"
    assert deferred == set()


def test_a_relative_import_is_never_a_vendor(mdep, tmp_path):
    """`from ..common import config` has `module` set, and must not match anything.

    `ast.ImportFrom.module` is `"common"` for a relative import, so a classifier reading
    that field without the vendor intersection would invent couplings. The intersection in
    `_vendors_in` is what prevents it; this test is what keeps the intersection there.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "rel.py").write_text("from ..common import config\nfrom . import sibling\n")

    rows, total = mdep.measure(pkg)

    assert total == 1
    assert rows == [], f"a relative import produced a vendor row: {rows}"


def test_a_module_importing_nothing_vendor_produces_no_row(mdep, tmp_path):
    """`state.py`'s shape: mentions vendors in PROSE, imports none of them.

    This is the case that made the grep counts meaningless -- state.py is the least coupled
    file in the package and a grep for `bedrock` ranks it alongside the AWS client.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "clean.py").write_text(
        '"""Talks about bedrock and boto3 and github at length."""\n'
        "import pydantic\n\n"
        "# boto3 is deliberately not imported here\n"
        "BEDROCK_MODEL = 'us.amazon.nova-2-lite-v1:0'\n"
    )

    rows, total = mdep.measure(pkg)

    assert total == 1
    assert rows == [], (
        f"a module that only MENTIONS vendors produced {rows}; the script would then be "
        f"measuring commentary, which is the defect it replaced"
    )


# ── the self-check, which must be able to fire ────────────────────────────────

def test_the_self_check_refuses_a_vacuous_split(mdep, tmp_path, capsys, monkeypatch):
    """`main` exits 1 when EVERY touching module reports a hard import.

    That pattern is what a leaked AST walk looks like, and it is exactly what the script's
    first version produced: a wrong answer that read as a right one. Measured by dropping
    the outer barrier in `_module_level`, which moved 1 hard / 3 deferred to 4 / 0.

    The guard is not decoration. A vacuous split reads as evidence, which CLAUDE.md
    identifies as worse than a missing measurement.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("import boto3\n")
    (pkg / "b.py").write_text("from github import Github\n")
    monkeypatch.setattr(mdep, "PACKAGE", pkg)

    code = mdep.main()

    assert code == 1, "two hard imports and no deferred one must be refused"
    assert "REFUSING" in capsys.readouterr().err


def test_the_self_check_passes_a_real_split(mdep, tmp_path, capsys, monkeypatch):
    """...and does not fire when the split is genuine, or it would refuse every codebase."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("import boto3\n")
    (pkg / "b.py").write_text("def f():\n    import boto3\n")
    monkeypatch.setattr(mdep, "PACKAGE", pkg)

    assert mdep.main() == 0
    assert "REFUSING" not in capsys.readouterr().err


# ── one assertion about the real package, deliberately weak ───────────────────

def test_the_real_package_is_measurable(mdep):
    """Asserts the measurement RUNS over `agentorg/` and finds something. Not what.

    A test naming today's counts would fail the moment a lane changed an import -- and
    would pass while the classifier itself broke, since the numbers are what it reads. So
    this pins only that the script has a live subject: `agentorg/` does couple to a vendor
    somewhere, and if these rows came back empty the script is measuring nothing.
    """
    rows, total = mdep.measure(mdep.PACKAGE)

    assert total > 20, f"only {total} modules found under agentorg/; wrong path?"
    assert rows, "no module under agentorg/ imports a vendor SDK, which cannot be true"
    assert any(hard for _, hard, _ in rows), (
        "no MODULE-LEVEL vendor import found anywhere; github_ops.py has one, so the "
        "classifier has stopped seeing them"
    )
