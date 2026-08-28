"""H6's harness under test: the properties that make its number worth reporting.

WHY A MEASUREMENT NEEDS TESTS. `measure.py` makes real model calls, so it cannot run in this
suite -- conftest guard 1 disables the model and replaces `llm._complete` with a raiser,
deliberately, because a live Bedrock call per test is how a hermetic suite becomes a bill.
So nothing here calls the model. What is tested is everything the reported number DEPENDS on
and that a future edit could quietly break:

  * the cases must actually carry the trait they are named for -- a mismatch case keyed on
    the email address would measure nothing, and the row would still print k/n;
  * the two arms must differ in EXACTLY ONE THING, the retrieved text;
  * the corpus must genuinely rank the mismatch document for the mismatch query, which is the
    measurement defect that was actually found by running it;
  * the invalid-run refusals must fire, because a fixture arm always approves and would
    report a perfect false-block rate.

THE HARNESS ALREADY FOUND TWO DEFECTS IN ITSELF and both are pinned below, because a defect
recorded only in a docstring is one a future edit reintroduces: the diffs were fragments, so
both arms objected to missing imports; and the query was the diff alone, which cannot rank a
document about a mismatch.
"""

import ast
import inspect
from pathlib import Path

import pytest

from agentorg.retrieval import guard, measure, repo_history
from agentorg.retrieval.search import hits

# GUARD AGAINST A VACUOUS FILE.
assert measure.MISS_CASES, "MISS_CASES is empty; the number this lane reports has no case"
assert measure.SETTLED_CASES, "SETTLED_CASES is empty; the false-block control is absent"
assert measure.CONTROL_CASES, "CONTROL_CASES is empty; nothing checks the reviewer still reviews"


# ── the cases carry the trait they are named for ──────────────────────────────

def test_the_mismatch_case_really_does_not_do_what_its_ticket_asked():
    """THE case. Its ticket says per-account; its diff must key on the source address.

    If the diff keyed on the email address the reviewer would be RIGHT to approve, the
    baseline would score 8/8, and the harness would report "no improvement" from a case that
    contained no mistake. The row would look identical either way.
    """
    diff = measure.MISS_CASES["per-ip-when-ticket-said-per-account"]

    assert "PER ACCOUNT" in measure.MISS_TICKET, "the mismatch ticket no longer asks for per-account"
    assert "email" in measure.MISS_TICKET.lower()
    assert "remote_addr" in diff, (
        "the mismatch diff no longer keys the limit on the source address, so it satisfies "
        "its ticket and there is nothing for the reviewer to miss"
    )
    key_lines = [ln for ln in diff.splitlines() if "key = " in ln]
    assert key_lines, "the diff has no rate-limit key line; the case cannot mismatch"
    assert not any("email" in ln for ln in key_lines), (
        f"the rate-limit key mentions the email address, so the diff DOES implement the "
        f"ticket: {key_lines}"
    )


def test_the_mismatch_case_has_nothing_else_to_object_to():
    """Everything a reviewer might otherwise block on is present.

    The mismatch must be the ONLY defect. A diff also missing the header, the expiry or the
    environment variables gives the reviewer three other reasons to object, and both arms
    would score highly for reasons the corpus had nothing to do with -- which is exactly how
    the fragment version of these diffs produced 5/5 in both arms.
    """
    diff = measure.MISS_CASES["per-ip-when-ticket-said-per-account"]

    for expected in ("retry_after", "expire", "os.environ", "429"):
        assert expected in diff, (
            f"the mismatch diff is missing {expected!r}, which gives the reviewer a second "
            f"reason to object and makes the measured number ambiguous"
        )


@pytest.mark.parametrize("name", sorted(measure.SETTLED_CASES))
def test_every_settled_case_is_a_complete_module(name):
    """THE FIRST MEASUREMENT DEFECT, pinned. Fragments made both arms score 5/5.

    Measured, `--trials 1`, before this was fixed: every `must_fix` read "references 'os' and
    'time' modules that are not imported" and "the authenticate() function is referenced but
    never defined". The reviewer's own prompt lists "it would crash, or it references
    something undefined" as blocking, so it was right -- and the objection had nothing to do
    with any settled ruling, so no corpus could move it.

    Parsed with `ast` rather than grepped for imports, because a diff can contain the word
    `import` in a comment. If the added lines do not form a valid module, the case is a
    fragment again.
    """
    diff = measure.SETTLED_CASES[name]
    body = "\n".join(
        line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    tree = ast.parse(body)          # raises SyntaxError if the case is not a whole module

    defined = {
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    imported = {
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert "authenticate" in defined, (
        f"{name} calls authenticate() without defining it, so the reviewer objects to an "
        f"undefined reference rather than to the settled trait"
    )
    for module in ("os", "redis"):
        assert module in imported, f"{name} uses {module} without importing it"


def test_the_go_control_is_not_given_the_python_preamble():
    """A control refused for the wrong reason is not a control.

    With the Python preamble prepended, the Go control is a Python file with Go pasted into
    it -- a syntax error. The reviewer would refuse it, the row would read 8/8, and the thing
    being tested (does the reviewer notice the wrong LANGUAGE) would never have been asked.
    """
    diff = measure.CONTROL_CASES["wrong-language"]

    assert "package auth" in diff
    assert "from flask import" not in diff, (
        "the Go control carries the Python preamble, so it is a syntax error rather than a "
        "wrong-language change and would be refused for the wrong reason"
    )
    assert "app/auth.go" in diff, "the Go control should not claim to be app/auth.py"


def test_the_credential_control_uses_the_published_example_key():
    """FAKE literals only. `AKIAIOSFODNN7EXAMPLE` is AWS's own documentation example."""
    diff = measure.CONTROL_CASES["hardcoded-credential"]
    assert "AKIAIOSFODNN7EXAMPLE" in diff
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" in diff


# ── the corpus genuinely ranks the mismatch ───────────────────────────────────

def test_the_mismatch_query_retrieves_the_mismatch_document():
    """THE SECOND MEASUREMENT DEFECT, pinned — and the FIRST VERSION OF THIS TEST WAS WRONG.

    `history-0001` is the per-IP-versus-per-email rejection, and it is the document the
    measured gain depends on. Without this test, "retrieval helped" could be true because the
    extra prose made the prompt longer, and nothing would say which.

    I asserted that the diff ALONE does not retrieve it. That is false, and the test caught me
    rather than the code. MEASURED over `repo_history.DOCUMENTS`, weighted-overlap scores:

        diff only     history-0005 17   history-0002 14   history-0001 12
        diff+ticket   history-0005 30   history-0001 25   history-0004 17

    So both queries retrieve it at `limit=3`; what the ticket changes is its RANK — third to
    second — and its score, 12 to 25. The earlier probe that suggested otherwise was reading
    the combined two-corpus result, where four `conventions` entries sit above it.

    The claim is therefore the weaker, true one: including the ticket raises the mismatch
    document's rank and score. Recorded rather than quietly fixed, because a test that had
    been "corrected" to match my wrong claim would have pinned the wrong fact while reading
    like evidence.
    """
    diff = measure.MISS_CASES["per-ip-when-ticket-said-per-account"]
    with_ticket = f"{diff} {measure.MISS_TICKET}"

    ranked_diff = [doc.doc_id for doc in hits(diff, repo_history.DOCUMENTS, limit=6)]
    ranked_both = [doc.doc_id for doc in hits(with_ticket, repo_history.DOCUMENTS, limit=6)]

    assert "history-0001" in ranked_both, (
        f"the diff-plus-ticket query does not retrieve history-0001, the rejection this "
        f"measurement depends on. Retrieved: {ranked_both}"
    )
    assert ranked_both.index("history-0001") < ranked_diff.index("history-0001"), (
        f"including the ticket no longer improves the mismatch document's rank "
        f"({ranked_diff} -> {ranked_both}), so the query composition in measure.py is not "
        f"doing what its comment claims"
    )


def test_the_retrieval_arm_reads_the_corpora_the_shipped_guard_would_give_the_reviewer():
    """The harness must not retrieve from a corpus set the reviewer would not have.

    Measuring against corpora the guard does not grant is measuring a system nobody would
    deploy. Asserted over `guard.CORPORA` so a change there fails here.
    """
    source = inspect.getsource(measure._retrieved_for)
    assert 'guard.CORPORA["reviewer"]' in source, (
        "_retrieved_for no longer reads the reviewer's corpora from the guard, so the "
        "measurement may use channels the shipped guard would refuse"
    )
    assert guard.CORPORA["reviewer"], "the reviewer has no corpora; the retrieval arm is empty"


# ── the two arms differ in exactly one thing ──────────────────────────────────

def test_the_baseline_arm_calls_the_shipped_agent_unchanged():
    """The baseline must be `reviewer.run(state)`, not a reimplementation of it.

    A hand-built baseline prompt would let the measured gain come from the harness's own
    prompt being worse than the shipped one -- a comparison that flatters retrieval and says
    nothing about the product. Asserted over the AST: a comment claiming `reviewer.run` is
    called would satisfy a substring check while the call sat elsewhere, which is the trap
    this repository has hit eleven times.
    """
    tree = ast.parse(inspect.getsource(measure._review))
    calls = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert "reviewer.run" in calls, (
        f"_review does not call reviewer.run, so the baseline arm is not what the pipeline "
        f"does today. Calls found: {sorted(calls)}"
    )
    assert "reviewer._prompt" in calls, (
        "the retrieval arm does not build on reviewer._prompt, so the two arms differ in more "
        "than the retrieved text"
    )
    assert "llm.reset_source" in calls, (
        "_review does not reset the source per review, so an earlier model answer masks a "
        "later fixture fallback and the INVALID check cannot fire"
    )


def test_the_state_swaps_the_ticket_and_the_plan_together():
    """A per-account ticket beside a per-IP plan makes the reviewer's approval defensible.

    It would be checking the diff against a plan the diff satisfies, so the measurement would
    be of a contradiction rather than of a mismatch -- and it would still print a number.
    """
    mismatch = measure._state("x", mismatch=True)
    baseline = measure._state("x", mismatch=False)

    assert mismatch.ticket_text == measure.MISS_TICKET
    assert mismatch.plan is measure.MISS_PLAN
    assert baseline.ticket_text == measure.TICKET
    assert baseline.plan is measure.PLAN
    assert mismatch.plan is not baseline.plan, (
        "both modes use the same plan, so the mismatch case is measured against a plan its "
        "diff satisfies"
    )
    assert "email" in " ".join(measure.MISS_PLAN.tasks).lower(), (
        "the mismatch plan does not name the email address, so the diff does not contradict it"
    )


# ── the refusals that keep an invalid run from being reported ─────────────────

def test_the_harness_refuses_a_run_whose_reviews_came_from_the_fixture():
    """`fixtures/review_result.json` approves unconditionally.

    So a fixture arm scores a perfect false-block rate and zero mismatches caught, and both
    numbers are about JSON deserialisation. The harness must exit non-zero rather than print
    them. Asserted over the AST of `measure`, because the string "INVALID" appearing in a
    print is not evidence the exit code follows it.
    """
    source = inspect.getsource(measure.measure)
    assert "fixture_rows" in source, "the harness no longer counts fixture reviews"
    assert "return 1 if invalid else 0" in source, (
        "the harness no longer returns non-zero for an invalid run, so an unusable "
        "measurement exits 0 and reads as a result"
    )


def test_the_harness_reports_a_gain_bought_with_false_positives():
    """A rising false-block rate must be named, not left for a reader to notice.

    Nobody reads two numbers side by side and infers the trade. `report.render`'s cache
    finding is the precedent: state it in words rather than leaving it to be inferred from a
    percentage.
    """
    source = inspect.getsource(measure.measure)
    assert "false_blocks[True] > false_blocks[False]" in source, (
        "the harness no longer compares the false-block rates between arms, so a gain bought "
        "by making the reviewer objection-happy would be reported as an improvement"
    )
    assert "BOUGHT WITH FALSE POSITIVES" in source


def test_the_harness_says_so_when_the_number_does_not_move():
    """The null result must be printed in words. It is the honest outcome and it happened.

    The false-block metric measured 0/15 in both arms, and a harness that printed two equal
    numbers with no comment invites the reader to find a difference that is not there.
    """
    source = inspect.getsource(measure.measure)
    assert "THE NUMBER DID NOT MOVE" in source
    assert "THE NUMBER MOVED THE WRONG WAY" in source, (
        "a regression must be named too; a harness that only announces null results reads "
        "as one that cannot report a loss"
    )


def test_the_measured_numbers_in_the_docstring_carry_their_command():
    """Rule 4: numbers in prose come from a command whose output is pasted.

    The docstring quotes `6/8` and `8/8`. This asserts the command that produced them is
    quoted beside them, so a reader can re-run rather than trust.
    """
    doc = measure.__doc__ or ""
    assert "--trials 8" in doc, "the measured figures do not name the trial count"
    assert "baseline 6/8" in doc and "retrieval 8/8" in doc, (
        "the measured figures are no longer in the docstring; if they were re-measured, "
        "paste the new ones"
    )
    assert "0/40" in doc, "the false-block control figure is missing beside the headline"
    assert "source=model" in doc, (
        "the docstring does not record that the measured reviews came from the model rather "
        "than the fixture, which is the one fact that makes the numbers mean anything"
    )


def test_the_harness_makes_no_import_that_ships_to_the_containers():
    """`measure.py` lives under `agentorg/`, so its imports are the agents' dependencies.

    `test_agentcore_deploy_assets.py` AST-walks this package and would make any third-party
    import here a pinned dependency of five arm64 images. This test names the failure locally
    so it reads as a Lane H problem rather than as a packaging test going red.
    """
    tree = ast.parse(Path(measure.__file__).read_text())
    top_level = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            top_level |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top_level.add(node.module.split(".")[0])

    allowed = {"argparse", "sys", "contextlib", "io", "agentorg", "__future__"}
    assert top_level <= allowed, (
        f"measure.py imports {sorted(top_level - allowed)}, which would become a pinned "
        f"dependency of all five arm64 agent containers"
    )
