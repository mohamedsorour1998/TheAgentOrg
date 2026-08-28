"""The evidence scripts, tested. Owner: Lane L.

`scripts/measure_*.py` produce every number that Lane L publishes into
`docs/final/evidence/`. A wrong number there is worse than a missing one: it
reads as evidence, and nothing downstream re-derives it. So the properties that
make those numbers trustworthy are pinned here rather than described in a
docstring.

WHY SEVERAL OF THESE ASSERT OVER THE AST AND NOT OVER THE SOURCE TEXT. This
repository has TWO recorded cases of a test satisfied by the comment explaining
the thing it was checking -- `deploy.yml`'s fixture-note literal and
`config.py`'s "SEVERITY_ORDER is imported, not restated". Both were caught only
by running the mutation, and both fixes have this shape. `measure_scorecard.py`
is 40% commentary and its comments name every symbol its behaviour depends on,
so a substring check here would be the third instance of that pattern rather
than a test.

THE ONE PROPERTY WORTH THE MOST. `agentorg.security.run_all_scanners` is
memoised on sha256 of the diff, and every run in one arm of the scorecard submits
the SAME diff. Without a cache clear per run, a 10-run arm scans ONCE and replays
a dict lookup nine times -- while all ten rows report `scan_provenance: scanners`
and `blocking: 2`, which is indistinguishable from ten real scans. MEASURED on
this script's first probe, 3 runs per arm:

    POISON-1 walk=1.5047    <- scanned
    POISON-2 walk=0.0556    <- cache
    POISON-3 walk=0.0591    <- cache

a 25x gap, and "3/3 blocked" was one measurement presented as three.
`test_the_scorecard_clears_the_scanner_cache_before_every_run` is the guard, and
it reads the AST because `_one_run`'s own docstring names
`reset_scanner_cache` three times.

THREE OF THESE TESTS PASSED ON THEIR OWN MUTATION AND HAD TO BE REWRITTEN, which
is worth recording because all three failed the same way: **the matcher's scope was
wider than the property.**

  1. `test_the_promotion_rule_has_a_no_regression_clause...` searched the whole
     document for "veto". Rewriting R1's body to make block correctness a tradeoff
     -- the exact thing the specification forbids -- left "veto" present in the §3
     preamble and in the rejection log, so the test passed. Now scoped to R1's own
     section, and it also forbids balancing language INSIDE that section.
  2. `test_the_scorecard_records_a_rejection_with_a_commit_that_exists` required
     that ONE sha resolve, over the whole file. The baseline commit is quoted in
     the header, so replacing every SHA in the rejection log with a plausible
     non-existent one left the test passing. Now scoped to the rejection section,
     and EVERY sha there must resolve.
  3. `test_the_scanner_update_process_covers_the_discriminator` -- the first
     mutation attempt renamed step 3's action while leaving its detail intact, and
     the test passed CORRECTLY, because the process did still cover the
     discriminator. The real mutation is deleting the step, and the test catches
     that. Recorded because it is the one case here where a passing mutation meant
     the test was right and the mutation was wrong -- distinguishing those two is
     the whole skill.

None of the three was visible by reading the test. Only running the mutation
showed it, which is why the RED step is mandatory rather than advisory.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
EVIDENCE = REPO_ROOT / "docs" / "final" / "evidence"

MEASURE_SCRIPTS = sorted(SCRIPTS.glob("measure_*.py"))


def _module(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    """The named top-level function, or a failure that says it is missing.

    Raises rather than returning None: a test whose subject does not exist must
    fail as "the thing I pin was renamed", not silently pass over an empty search.
    """
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(
        f"no top-level function {name!r}; this test would otherwise pin nothing"
    )


def _called_names(node: ast.AST) -> set[str]:
    """Every function name called anywhere inside `node`.

    Bare names and attribute tails both, so `reset_scanner_cache()` and
    `security.reset_scanner_cache()` are found the same way. Comments and
    docstrings are invisible here, which is the entire point.
    """
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        target = child.func
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


# --------------------------------------------------------------------------
# The scripts exist and are self-consistent
# --------------------------------------------------------------------------

def test_there_is_at_least_one_measure_script():
    """Guard for every parametrised test below, which would otherwise pin nothing.

    The operational form of this repository's standing rule: a matcher that can
    match nothing must assert that it matched.
    """
    assert MEASURE_SCRIPTS, (
        "no scripts/measure_*.py found; every parametrised test in this file "
        "would collect zero cases and pass"
    )


@pytest.mark.parametrize("script", MEASURE_SCRIPTS, ids=lambda p: p.name)
def test_every_measure_script_parses_and_has_a_main(script: pathlib.Path):
    """A script that cannot be run cannot be the source of a published number."""
    tree = _module(script)
    names = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    assert "main" in names, f"{script.name} has no main(); nothing can invoke it"


@pytest.mark.parametrize("script", MEASURE_SCRIPTS, ids=lambda p: p.name)
def test_every_measure_script_runs_and_exits_zero(
    script: pathlib.Path, tmp_path: pathlib.Path
):
    """The script IS the evidence, so it has to actually run.

    Written as a subprocess deliberately. Importing and calling `main()` in this
    process would run the measurement INSIDE pytest, where conftest's six autouse
    guards are bound -- and the whole reason `measure_scorecard.py` forces its own
    regime is that those guards do not bind outside pytest. Testing it in-process
    would exercise a configuration no published number is ever produced under.

    `--out` goes to `tmp_path`, not to `runs/`. An earlier version wrote three
    files into `runs/` -- gitignored, so nothing complained, and shared with every
    other lane and with ~10k real run records. A test's output belongs somewhere
    the test owns; `tmp_path` is removed for us.

    `--runs 1` keeps this to one walk per arm. The scorecard's own default is 10.
    """
    args = [sys.executable, str(script), "--out", str(tmp_path / f"{script.stem}.json")]
    if script.name == "measure_scorecard.py":
        args += ["--runs", "1"]

    result = subprocess.run(
        args, capture_output=True, text=True, check=False, cwd=str(REPO_ROOT),
        timeout=600,
    )
    assert result.returncode == 0, (
        f"{script.name} exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-3000:]}\n"
        f"--- stderr ---\n{result.stderr[-3000:]}"
    )
    assert result.stdout.strip(), f"{script.name} printed nothing; it must show its work"


# --------------------------------------------------------------------------
# The scorecard's regime, which is what makes its numbers reproducible
# --------------------------------------------------------------------------

def test_the_scorecard_clears_the_scanner_cache_before_every_run():
    """The most important property in this file. See the module docstring.

    ASSERTED OVER THE AST. `_one_run`'s docstring names `reset_scanner_cache`
    three times while explaining why it is called, so `assert
    "reset_scanner_cache" in source` passes with the CALL deleted -- and with the
    call deleted, nine of every ten scorecard rows are a dict lookup reported as
    a scan.

    The call must also precede `run_pipeline`: clearing the cache after the walk
    clears it for the NEXT run rather than this one, which is a real and subtle
    difference. On the first run of a fresh process the cache is empty either way,
    so a one-run measurement cannot tell them apart -- the ordering is the
    requirement, and the ordering is what is pinned.
    """
    tree = _module(SCRIPTS / "measure_scorecard.py")
    one_run = _function(tree, "_one_run")

    assert "reset_scanner_cache" in _called_names(one_run), (
        "_one_run does not CALL reset_scanner_cache. Every run in one arm submits "
        "the same diff, so run_all_scanners' sha256 memo answers all but the "
        "first -- and every row still reports scan_provenance 'scanners'."
    )

    # Positions, over the AST, so the ordering claim is not a text search either.
    resets = [
        node.lineno
        for node in ast.walk(one_run)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "reset_scanner_cache"
    ]
    walks = [
        node.lineno
        for node in ast.walk(one_run)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_pipeline"
    ]
    assert resets and walks, (
        f"expected both calls in _one_run; found resets={resets} walks={walks}"
    )
    assert min(resets) < min(walks), (
        f"reset_scanner_cache is called at line {min(resets)}, AFTER run_pipeline "
        f"at line {min(walks)}. It must clear the cache for THIS walk, not the next."
    )


def test_the_scorecard_forces_both_halves_of_the_offline_regime():
    """OFFLINE alone leaves the model live, and that is a measured trap.

    `llm.available()` reads LLM_DISABLED, LLM_BASE_URL and boto3 credentials, and
    never OFFLINE -- CLAUDE.md records `OFFLINE=true python -c '...available()'`
    printing True. So a script that set only OFFLINE would make a live billable
    Bedrock call per agent per run while looking hermetic.

    Asserted over the assigned STRING KEYS in `_force_the_hermetic_regime`, not
    over the file's text: the module docstring explains this trap by name and
    would satisfy any substring check.
    """
    tree = _module(SCRIPTS / "measure_scorecard.py")
    forcer = _function(tree, "_force_the_hermetic_regime")

    keys = {
        node.value
        for node in ast.walk(forcer)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for required in ("OFFLINE", "LLM_DISABLED"):
        assert required in keys, (
            f"_force_the_hermetic_regime never names {required!r}. Closing only "
            f"one of the two seams leaves the other live: OFFLINE does not "
            f"disable the model, and LLM_DISABLED does not close GitHub."
        )


def test_the_scorecard_refuses_to_measure_when_the_model_is_still_live():
    """A guard that reports rather than proceeds, and it must RAISE.

    If forcing the regime somehow fails, the alternative to raising is a run of
    live billable calls whose numbers cannot be reproduced by anyone else. The
    check exists in `_force_the_hermetic_regime`; this pins that its failure path
    is a raise and not a warning.
    """
    tree = _module(SCRIPTS / "measure_scorecard.py")
    forcer = _function(tree, "_force_the_hermetic_regime")

    raises = [node for node in ast.walk(forcer) if isinstance(node, ast.Raise)]
    assert raises, (
        "_force_the_hermetic_regime has no raise. A failure to close the model "
        "seam must stop the measurement, not warn about it -- a warning above a "
        "published number is read as noise."
    )

    calls = _called_names(forcer)
    assert "available" in calls, (
        "_force_the_hermetic_regime never calls llm.available(), so it asserts "
        "nothing about whether the seam it just closed is actually closed"
    )


def test_the_scorecard_reports_a_spread_and_never_a_bare_point_value():
    """`CLAUDE.md`: 116.88 -> 149.68 -> 102.83 s for one unchanged snapshot.

    So a timing is a range plus its conditions. `_spread` is the only path any
    timing takes into the report, and it must carry all three of min, median and
    max -- a helper that returned only a mean would make every published timing a
    point value again, with no test failing.
    """
    tree = _module(SCRIPTS / "measure_scorecard.py")
    spread = _function(tree, "_spread")

    keys = {
        node.value
        for node in ast.walk(spread)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for required in ("min", "median", "max", "n"):
        assert required in keys, f"_spread does not report {required!r}"


def test_an_unmeasurable_dimension_carries_its_reason_and_a_command():
    """An honest gap beats an invented number, but only if it says what is missing.

    Every entry in UNMEASURED needs three things: `measured: False`, a `why`, and
    the command a human would run. A gap with no command is a note; a gap with a
    command is a task.
    """
    scorecard = _load_scorecard_module()

    assert scorecard.UNMEASURED, "UNMEASURED is empty; this test would pin nothing"
    for name, row in scorecard.UNMEASURED.items():
        assert row["measured"] is False, f"{name} is in UNMEASURED but claims measured"
        assert row["why"].strip(), f"{name} has no reason"
        assert row["command_a_human_would_run"].strip(), (
            f"{name} names no command, so nothing tells the next person how to "
            f"close the gap"
        )


def _load_scorecard_module():
    """Import `scripts/measure_scorecard.py` without executing its measurement.

    Import is safe: the module does its work in `main()`, and `_force_the_hermetic
    _regime` runs only from `measure()`. Guarded so a future refactor that moves
    work to import time fails here rather than making the suite do a measurement.
    """
    import importlib.util

    path = SCRIPTS / "measure_scorecard.py"
    spec = importlib.util.spec_from_file_location("_measure_scorecard", path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_rate_helper_returns_none_rather_than_zero_for_no_denominator():
    """Zero reads as a measured answer; None reads as an absent one.

    Same distinction `CostRecord.usd` makes for the same reason. A false-block
    rate of 0.0 over zero runs is the flattering direction, which is exactly why
    it must not be expressible.
    """
    scorecard = _load_scorecard_module()

    assert scorecard._rate(0, 0) is None, (
        "a rate with no denominator returned a number; 0.0 there would publish "
        "a perfect score measured over nothing"
    )
    assert scorecard._rate(0, 10) == 0.0, "a real zero must still be reportable"
    assert scorecard._rate(10, 10) == 1.0


# --------------------------------------------------------------------------
# The evidence documents themselves
# --------------------------------------------------------------------------

def test_the_evidence_directory_exists_and_is_not_empty():
    """Lane L's deliverable is the directory. An empty one is the lane not done."""
    assert EVIDENCE.is_dir(), f"{EVIDENCE} does not exist"
    contents = sorted(p.name for p in EVIDENCE.iterdir())
    assert contents, f"{EVIDENCE} is empty"


def test_every_published_json_artifact_records_its_conditions():
    """A number without its conditions is not a measurement. See CLAUDE.md rule 4.

    Each JSON artifact must name the commit it describes and the regime it was
    measured under. The scanner mode is the one that matters most: both modes
    block the poisoned ticket with blocking=2, so a row that does not say which
    mode produced it is reporting two different claims under one number.

    THE FIELDS ARE LOOKED UP AT THE TOP LEVEL **OR** UNDER `_agentorg`, because
    `sbom.json` is a CycloneDX document and its top level belongs to that schema
    rather than to us. Adding `commit` beside `bomFormat` would produce a document
    that no longer validates -- so the SBOM carries the same three facts in a
    namespaced key, and this test accepts either location rather than forcing a
    standard artifact to be malformed to satisfy a convention.
    """
    artifacts = sorted(EVIDENCE.glob("*.json"))
    assert artifacts, (
        f"no JSON artifacts in {EVIDENCE}; this test would pin nothing"
    )
    for path in artifacts:
        data = json.loads(path.read_text(encoding="utf-8"))
        namespaced = data.get("_agentorg", {})
        for field in ("commit", "measured_at"):
            assert field in data or field in namespaced, (
                f"{path.name} records no {field}, at the top level or under "
                f"_agentorg"
            )
        conditions = data.get("conditions") or namespaced.get("conditions")
        assert isinstance(conditions, dict) and conditions, (
            f"{path.name} records no conditions"
        )


def test_the_scorecard_artifact_agrees_with_the_scanner_mode_it_recorded():
    """The published row must not claim real scanners while carrying fixture lines.

    THE DISCRIMINATOR IS THE LINE-NUMBER SET, and it is the only field that
    differs: real scanners report {3, 4} and the fixture reports {4, 5}. The two
    sets OVERLAP AT LINE 4, so this compares whole sets and never an individual
    finding -- no single-line observation can separate the modes.

    Imported from `tests/provenance.py` rather than restated. A second copy of
    these two sets would be a second declaration of the fact this repository's
    whole verification story rests on, and both copies would keep passing as they
    drifted apart.
    """
    from tests import provenance as prov

    path = EVIDENCE / "scorecard-baseline.json"
    if not path.exists():
        pytest.skip(f"{path.name} not generated yet")

    data = json.loads(path.read_text(encoding="utf-8"))
    mode = data["conditions"]["scanner_mode"]
    poisoned_rows = [row for row in data["rows"] if row["poisoned"]]
    assert poisoned_rows, "the artifact carries no poisoned rows to check"

    for row in poisoned_rows:
        lines = frozenset(row["finding_lines"])
        if not lines:
            continue
        if row["provenance_mode"] == "real_scanners":
            assert lines == prov.REAL_SCANNER_LINES, (
                f"{row['ticket_id']} claims real scanners but reports lines "
                f"{sorted(lines)}, not {sorted(prov.REAL_SCANNER_LINES)}. Either "
                f"the reference diff moved -- a single removed blank line shifts "
                f"every finding below it -- or the fan-out fell back. Do not "
                f"guess; re-measure."
            )
        elif row["provenance_mode"] == "fixture":
            assert lines == prov.FIXTURE_LINES, (
                f"{row['ticket_id']} claims the fixture but reports lines "
                f"{sorted(lines)}, not {sorted(prov.FIXTURE_LINES)}"
            )

    assert "REAL-SCANNER" in mode or "FIXTURE" in mode or "HALF" in mode, (
        f"unrecognised scanner mode string {mode!r}; provenance.describe_mode "
        f"changed and this check no longer knows what it is reading"
    )


# --------------------------------------------------------------------------
# The prose documents. Their CONTENT is judged by a human; these tests pin the
# structural properties a reader depends on and a careless edit removes.
# --------------------------------------------------------------------------

# The four documents Lane L owns, and the one property each exists to carry. A
# document that lost its property still reads well, which is exactly why this is a
# test and not a review checklist.
REQUIRED_DOCUMENTS = {
    "scorecard.md": "block correctness",
    "dependency-inventory.md": "load-bearing",
    "sbom.md": "scanner-update process",
    "limitations.md": "what removing it would take",
}


@pytest.mark.parametrize("filename", sorted(REQUIRED_DOCUMENTS))
def test_each_evidence_document_exists_and_carries_its_subject(filename: str):
    path = EVIDENCE / filename
    assert path.exists(), f"{filename} is missing from {EVIDENCE}"
    text = path.read_text(encoding="utf-8")
    marker = REQUIRED_DOCUMENTS[filename]
    assert marker.lower() in text.lower(), (
        f"{filename} does not mention {marker!r}, which is the one thing it exists "
        f"to say"
    )


def test_the_promotion_rule_has_a_no_regression_clause_on_block_correctness():
    """The spec's hard requirement, and the scorecard's veto.

    Specification §3: the promotion rule "must include a no-regression clause on
    block correctness -- the gate is the product, and a release that ships faster
    while blocking less is a worse release."

    SCOPED TO R1's OWN SECTION, and the first version of this test was not --
    which made it pass on a real mutation. Rewriting R1's body to say the clause
    is "weighed against the other four... considered on balance" -- turning the
    veto into a tradeoff, the exact thing §3 forbids -- left the words "veto" and
    "outranks" present ELSEWHERE in the document (in the §3 preamble and in the
    rejection log), so a whole-file search still found them and the test passed.

    Caught by running the mutation, not by reading the test. It is this
    repository's most repeated failure shape: a matcher whose scope is wider than
    the property it checks.
    """
    text = (EVIDENCE / "scorecard.md").read_text(encoding="utf-8")
    lowered = text.lower()

    assert "no regression" in lowered or "no-regression" in lowered, (
        "the promotion rule has no no-regression clause"
    )

    # R1's section only: from its heading to the next heading of any level.
    match = re.search(
        r"^#+ R1[^\n]*\n(.*?)(?=^#+ )", text, flags=re.MULTILINE | re.DOTALL
    )
    assert match, (
        "no R1 section found in scorecard.md. This test pins the content of that "
        "section, so it must fail rather than search the whole document."
    )
    r1 = match.group(1).lower()

    assert "must not decrease" in r1 or "must never fall" in r1, (
        f"R1 does not say the direction it forbids. R1 reads:\n{r1[:400]}"
    )
    assert "veto" in r1 or "outranks" in r1, (
        f"R1 names block correctness but does not give it precedence WITHIN ITS "
        f"OWN SECTION. A clause that trades off against the other four is not a "
        f"no-regression clause -- a release could buy a lower block rate with a "
        f"faster time to merge. R1 reads:\n{r1[:400]}"
    )
    # The explicit negation, because "outranks" can be asserted and then undercut
    # by the sentence after it.
    assert "on balance" not in r1 and "weighed against" not in r1, (
        f"R1 contains balancing language, which contradicts a veto. R1 reads:\n"
        f"{r1[:400]}"
    )


def test_the_scorecard_records_a_rejection_with_a_commit_that_exists():
    """A rubric that has never rejected anything is decoration. See spec §3.

    The commit SHAs are RESOLVED against git rather than pattern-matched. A
    document can quote a plausible-looking SHA that names nothing, and a reader
    checking it would find that out at the worst possible moment -- in front of the
    person who asked.

    TWO THINGS THE FIRST VERSION OF THIS TEST GOT WRONG, both found by mutation:

      1. It searched the WHOLE document and required only that ONE SHA resolve.
         The baseline commit is quoted in the header, so replacing every SHA in the
         rejection log with a plausible non-existent one left the test PASSING --
         `9b2b1ee` alone satisfied it. Scoped to the rejection section now, and
         EVERY sha-shaped string there must resolve.
      2. Run ids look like SHAs to a `[0-9a-f]{7,40}` pattern. `32557597915` is a
         GitHub run id, is pure digits, and is not a commit -- so a naive "all must
         resolve" would fail on a correct document. Pure-digit strings of 10+
         characters are excluded, and the exclusion is asserted to be non-empty so
         it cannot quietly swallow everything.
    """
    text = (EVIDENCE / "scorecard.md").read_text(encoding="utf-8")

    match = re.search(
        r"^## 4 · (.*?)(?=^## \d+ · |\Z)", text, flags=re.MULTILINE | re.DOTALL
    )
    assert match, (
        "no '## 4 ·' rejection section in scorecard.md. The recorded rejection is "
        "a specification requirement, so its absence must fail here."
    )
    section = match.group(1)

    candidates = set(re.findall(r"`([0-9a-f]{7,40})`", section))
    # Run ids are decimal and long; commit SHAs of that length would be extremely
    # unusual. Excluded so a correct document does not fail on them.
    run_ids = {c for c in candidates if c.isdigit() and len(c) >= 10}
    shas = candidates - run_ids

    assert shas, (
        f"the rejection section quotes no commit SHA, so its rejection is "
        f"unsourced. Found only these non-SHA candidates: {sorted(run_ids)}"
    )

    unresolved = []
    for sha in sorted(shas):
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "cat-file", "-t", sha],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 or result.stdout.strip() != "commit":
            unresolved.append(sha)

    assert not unresolved, (
        f"the rejection log quotes SHAs that do not resolve to commits in this "
        f"repository: {unresolved}. A rejection log whose commits do not exist is "
        f"worse than no rejection log -- a reader checks one, finds nothing, and "
        f"stops believing the rest."
    )


def test_every_unmeasured_dimension_in_the_document_matches_the_script():
    """The prose and the script must not disagree about what was measured.

    This is the failure the whole lane is written against: a document claiming a
    number the script does not produce. Compared against the SCRIPT's UNMEASURED
    keys rather than against a list retyped here -- a second list would be a second
    declaration, and both would keep passing as they drifted.
    """
    scorecard = _load_scorecard_module()
    text = (EVIDENCE / "scorecard.md").read_text(encoding="utf-8")

    assert scorecard.UNMEASURED, "UNMEASURED is empty; this test would pin nothing"
    for name in scorecard.UNMEASURED:
        readable = name.replace("_", " ")
        assert readable in text.lower(), (
            f"the script reports {name!r} as NOT measured and the document never "
            f"mentions it. A reader would take the document's silence for a "
            f"measurement that exists."
        )


def test_every_limitation_is_costed():
    """"A limitation is only credible if it is costed" -- specification §13.

    Each numbered entry must say what removing it would take. Counted per SECTION
    rather than once for the file: a document with nine limitations and one costing
    would satisfy any whole-file check while eight entries stayed uncosted.
    """
    text = (EVIDENCE / "limitations.md").read_text(encoding="utf-8")

    # Numbered `## N ·` headings are the entries. The separator is part of the
    # pattern so a stray "## What is NOT on this list" is not counted as one.
    sections = re.split(r"^## \d+ · ", text, flags=re.MULTILINE)[1:]
    assert len(sections) >= 7, (
        f"specification §13 carries seven limitations forward; the document has "
        f"{len(sections)} numbered entries"
    )

    for section in sections:
        title = section.splitlines()[0]
        lowered = section.lower()
        assert "cost to remove" in lowered, (
            f"the limitation {title!r} is not costed. An admitted limitation is "
            f"weaker than a costed one."
        )
        assert "why not now" in lowered or "why it is listed" in lowered or (
            "why this is listed" in lowered
        ), (
            f"the limitation {title!r} says what removing it would take but not "
            f"why that is not this phase's priority"
        )


def test_the_sbom_says_it_is_not_an_image_scan():
    """The one thing that would make this SBOM dishonest is silence about its kind.

    A source-declared SBOM presented as an image scan under-reports, and a consumer
    joining it against a CVE feed would believe it had covered the transitive
    closure. Asserted on BOTH artifacts, because a machine-readable file is read by
    machines: a caveat that lives only in the markdown does not reach them.
    """
    markdown = (EVIDENCE / "sbom.md").read_text(encoding="utf-8")
    assert "not an image scan" in markdown.lower(), (
        "sbom.md does not say it is not an image scan"
    )

    document = json.loads((EVIDENCE / "sbom.json").read_text(encoding="utf-8"))
    properties = {
        prop["name"]: prop["value"]
        for prop in document["metadata"].get("properties", [])
    }
    assert properties.get("agentorg:sbom_kind") == "source-declared", (
        f"sbom.json does not declare its kind in metadata.properties; found "
        f"{sorted(properties)}"
    )


def test_every_sbom_gap_names_a_command():
    """A gap with a command is a task; a gap without one is an excuse."""
    document = json.loads((EVIDENCE / "sbom.json").read_text(encoding="utf-8"))
    gaps = document["_agentorg"]["gaps"]
    assert gaps, "the SBOM claims no gaps, which for a source-declared SBOM is false"
    for gap in gaps:
        assert gap["command"].strip(), f"the gap {gap['gap']!r} names no command"
        assert gap["why"].strip(), f"the gap {gap['gap']!r} gives no reason"


def test_the_scanner_update_process_covers_the_discriminator():
    """A scanner bump can move a line number, and that is the whole risk.

    `REAL_SCANNER_LINES` is {3, 4} and `FIXTURE_LINES` is {4, 5}. A gitleaks update
    that shifted the real pair onto {4, 5} would leave the two sets IDENTICAL --
    every provenance assertion in the suite would keep passing while proving
    nothing. So the update process is not a version bump; it must re-measure the
    discriminator, and that step must be present.
    """
    document = json.loads((EVIDENCE / "sbom.json").read_text(encoding="utf-8"))
    steps = document["_agentorg"]["update_process"]
    assert steps, "the SBOM records no scanner-update process"

    joined = " ".join(
        f"{step['action']} {step['detail']} {step['verify']}" for step in steps
    ).lower()

    assert "discriminator" in joined, (
        "the scanner-update process never mentions the line-number discriminator, "
        "which is the one thing a scanner bump can silently destroy"
    )
    assert "provenance" in joined, (
        "the update process does not re-measure provenance"
    )
    # Both files must be named, because they are two declarations of one fact and a
    # bump that moves one produces an image whose scanner differs from the one the
    # suite was gated against.
    assert "dockerfile" in joined and "ci.yml" in joined, (
        "the update process does not name BOTH places a scanner version lives"
    )

    numbers = [step["step"] for step in steps]
    assert numbers == sorted(numbers), (
        f"the update process steps are not in order: {numbers}. The order is the "
        f"content -- re-measuring the discriminator after deploying is too late."
    )
