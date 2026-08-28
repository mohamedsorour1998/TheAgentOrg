"""H1: the retrieval interface and its provenance. Lane H.

WHAT THIS FILE IS FOR: the provenance vocabulary is the half of Lane H that outlives any
particular corpus, and its whole value is that four facts stay four facts. `documents == 0`
reads identically for "searched and matched nothing", "nobody asked", and "the corpus
raised" -- so the tests that matter here are the ones that would fail if any two of those
collapsed.

THE BOUNDARY TESTS ARE NOT HERE. They live in `tests/test_retrieval_boundary.py`, which
attempts the breach rather than asserting isolation. This file covers the interface: the
encoding, the refusals around it, the search ranking's determinism, and the honest limit.
"""

import pytest

from agentorg.common import config
from agentorg.retrieval import advisories, conventions, guard, provenance, repo_history
from agentorg.retrieval.search import Document, hits, render, tokenise

# GUARD AGAINST A VACUOUS FILE. Every corpus test below iterates one of these.
CORPORA = {
    repo_history.NAME: repo_history.DOCUMENTS,
    conventions.NAME: conventions.DOCUMENTS,
    advisories.NAME: advisories.DOCUMENTS,
}
for _name, _docs in CORPORA.items():
    assert _docs, f"corpus {_name} is empty; every test over it would pin nothing"


# ── the four values stay four values ──────────────────────────────────────────

def test_the_three_no_document_outcomes_are_three_different_values():
    """THE test for this vocabulary. `empty`, `disabled` and `unavailable` all yield zero
    documents, and they are three different facts wanting three different responses:
    accept it, turn the knob on, go and fix the corpus.

    Asserted as pairwise distinctness rather than by naming the values, because a future
    edit that made two of them the same string would satisfy any test checking that each
    is present.
    """
    outcomes = [provenance.EMPTY, provenance.DISABLED, provenance.UNAVAILABLE]
    assert len(set(outcomes)) == 3, (
        f"two of the zero-document outcomes are the same value ({outcomes}), so a reader "
        f"cannot tell a healthy empty corpus from a switched-off retriever or a broken one"
    )
    assert provenance.A_FAULT & provenance.A_CHOICE == frozenset(), (
        "a value is classified as both a fault and a choice; that is the fixture-fallback "
        "versus fixture-stub distinction collapsing"
    )
    assert provenance.UNAVAILABLE in provenance.A_FAULT
    assert provenance.DISABLED in provenance.A_CHOICE
    assert provenance.EMPTY not in provenance.A_FAULT | provenance.A_CHOICE, (
        "an empty corpus is neither a fault nor a choice -- it is a fact about the query"
    )


def test_the_unknown_value_is_not_writable():
    """Nothing may WRITE the blank. It exists only for records that predate the encoding.

    Same rule as `ScanProvenanceOrUnknown`: the blank is a separate alias precisely so a
    writer cannot reach it. `encode` refuses it, which is what makes a blank in a real
    record readable as "old row" rather than "somebody wrote unknown".
    """
    assert provenance.UNKNOWN not in provenance.VALUES
    with pytest.raises(ValueError):
        provenance.encode("advisories", provenance.UNKNOWN)


def test_a_bare_corpus_name_decodes_to_unknown_and_never_to_retrieved():
    """The legacy shape. A record naming a corpus is not evidence that corpus answered.

    Reading a bare name as `retrieved` is the falsy-value trap that
    `ci_status_measured` documents: a run with a dead corpus would be indistinguishable
    from a run with a live one, and every provenance column would read as evidence.
    """
    name, value = provenance.decode("repo-history")
    assert name == "repo-history"
    assert value == provenance.UNKNOWN, (
        f"a bare corpus name decoded to {value!r}; it must be unknown, because nothing in "
        f"the entry says the corpus answered"
    )
    assert value != provenance.RETRIEVED
    assert value != provenance.EMPTY


@pytest.mark.parametrize("value", sorted(provenance.VALUES))
def test_encode_and_decode_round_trip(value):
    """One writer, one reader, and they agree. Two spellings is how they drift."""
    assert provenance.decode(provenance.encode("advisories", value)) == ("advisories", value)


def test_encode_refuses_a_corpus_name_that_would_decode_to_something_else():
    """A name carrying the separator produces an entry that decodes wrong -- silently.

    The decode would not raise; it would return a plausible corpus name and a provenance
    value that came from the middle of the original name. That is worse than a crash.
    """
    with pytest.raises(ValueError):
        provenance.encode(f"repo{provenance.SEPARATOR}history", provenance.RETRIEVED)
    with pytest.raises(ValueError):
        provenance.encode("", provenance.RETRIEVED)


def test_describe_reports_an_unrecognised_value_as_unrecognised():
    """A renderer that prints "unknown" for a value it does not know is a mislabelled metric.

    A mislabelled metric is worse than a missing one: it reads as evidence. So an
    unexpected value is named in the output rather than mapped to a default.
    """
    assert "unrecognised" in provenance.describe("verified")
    assert "unknown" in provenance.describe(provenance.UNKNOWN)
    for value in provenance.VALUES:
        assert "unrecognised" not in provenance.describe(value), (
            f"describe({value!r}) reports a legal value as unrecognised"
        )


# ── the disabled path, which is the shipped default ───────────────────────────

def test_with_retrieval_disabled_the_record_still_names_the_corpora(monkeypatch):
    """`RETRIEVAL_ENABLED` is false by default, and that must not produce a BLANK record.

    "disabled" against a named corpus and an empty `corpora` list are different facts: the
    first says which corpora would have been consulted, the second says nothing at all, and
    a demo whose corpus was never loaded would look like a run with no corpora configured.
    """
    monkeypatch.setattr(config, "RETRIEVAL_ENABLED", False)
    text, entries, count = guard.context_for("reviewer", "Retry-After header")

    assert text == "", "retrieval is disabled but returned prompt text"
    assert count == 0
    assert entries, "the record is blank; it must still name the corpora that were skipped"
    for entry in entries:
        assert provenance.decode(entry)[1] == provenance.DISABLED, (
            f"entry {entry!r} does not report `disabled` with retrieval switched off"
        )


def test_the_knob_is_read_through_the_module_and_not_bound_at_import(monkeypatch):
    """A bare imported name binds the value at import, before any fixture runs.

    Then the knob silently ignores both the tests and the deployed environment -- the trap
    CLAUDE.md names for `SCANNERS_REQUIRED` and `LLM_DISABLED`. This test passes only if
    `guard` reads `config.RETRIEVAL_ENABLED` at call time.
    """
    monkeypatch.setattr(config, "RETRIEVAL_ENABLED", False)
    _, disabled_entries, _ = guard.context_for("reviewer", "Retry-After header")
    monkeypatch.setattr(config, "RETRIEVAL_ENABLED", True)
    _, enabled_entries, count = guard.context_for("reviewer", "Retry-After header")

    assert disabled_entries != enabled_entries, (
        "flipping config.RETRIEVAL_ENABLED changed nothing, so guard.py bound the value at "
        "import and the knob is inert"
    )
    assert count > 0, "retrieval is enabled and the query matched nothing; see the corpus"


# ── a corpus that raises is a FAULT, not an empty result ──────────────────────

def test_a_corpus_that_raises_is_recorded_as_unavailable(monkeypatch):
    """The distinction that hides a broken corpus if it is lost.

    A loader that raises must NOT be recorded as `empty`. `empty` means the corpus is
    loaded and healthy and this question has no answer in it; `unavailable` means go and
    fix something. Collapsing them is `fixture-fallback` collapsing into `fixture-stub`.
    """
    monkeypatch.setattr(config, "RETRIEVAL_ENABLED", True)

    def boom():
        raise OSError("corpus file is unreadable")

    monkeypatch.setitem(guard._LOADERS, conventions.NAME, boom)
    _, entries, count = guard.context_for("reviewer", "Retry-After header")

    by_corpus = dict(provenance.decode(entry) for entry in entries)
    assert by_corpus[conventions.NAME] == provenance.UNAVAILABLE, (
        f"a raising corpus was recorded as {by_corpus[conventions.NAME]!r}; a fault must "
        f"not be recorded as an empty result"
    )
    assert by_corpus[repo_history.NAME] != provenance.UNAVAILABLE, (
        "one corpus raising marked the others unavailable too; each corpus reports its own "
        "outcome or the record cannot say which one is broken"
    )
    assert count >= 0


def test_a_query_that_matches_nothing_is_empty_and_not_unavailable(monkeypatch):
    """The other side of the same distinction, and the corpora must be healthy for it.

    The query is deliberately about something no corpus discusses. The first attempt used
    the word "token", which MATCHED -- three advisories discuss API tokens as credentials.
    Recorded because it is the correct behaviour mistaken for a bad test: a "nonsense" query
    that shares one real word with the corpus is not a nonsense query.
    """
    monkeypatch.setattr(config, "RETRIEVAL_ENABLED", True)
    _, entries, count = guard.context_for("reviewer", "marmalade preserves citrus jars")

    assert count == 0, "the unrelated query matched something; pick a different one"
    for entry in entries:
        assert provenance.decode(entry)[1] == provenance.EMPTY, (
            f"entry {entry!r}: a healthy corpus that matched nothing must report `empty`"
        )


# ── search: determinism, and the stated limit ─────────────────────────────────

def test_the_ranking_is_deterministic_across_input_order():
    """Ties break on `doc_id`, always.

    A ranking whose order depends on list order changes when a corpus file is re-saved, and
    then H6's before/after measures the ranker rather than the corpus.
    """
    query = "aws access key credential"
    forward = [doc.doc_id for doc in hits(query, list(advisories.DOCUMENTS), limit=4)]
    reverse = [doc.doc_id for doc in hits(query, list(reversed(advisories.DOCUMENTS)), limit=4)]

    assert forward, "the query matched nothing; this test would pin nothing"
    assert forward == reverse, (
        f"reversing the corpus changed the ranking: {forward} vs {reverse}. A "
        f"nondeterministic ranker makes every measured before/after unreproducible."
    )


def test_a_blank_query_retrieves_nothing_rather_than_arbitrary_documents():
    """Returning the first N for an empty query puts unrelated text in a prompt AND records
    `retrieved` -- so the record claims a successful retrieval for a question nobody asked.
    """
    assert hits("", advisories.DOCUMENTS) == []
    assert hits("the and of a", advisories.DOCUMENTS) == [], (
        "a query of nothing but stopwords retrieved documents"
    )


def test_zero_overlap_documents_are_dropped_not_returned_with_score_zero():
    """A document that matched nothing is not a weak hit.

    Including it makes `documents` in the provenance record count documents nobody
    retrieved, which is the count lying about itself.
    """
    unrelated = Document(doc_id="z", title="Marmalade", body="citrus preserves in jars")
    relevant = Document(
        doc_id="a", title="AWS keys", body="aws access key", keywords=("aws",)
    )
    assert hits("aws access key", [unrelated, relevant]) == [relevant]


def test_the_synonym_limit_is_real_and_this_test_records_it():
    """THE HONEST LIMIT, pinned so it is visible in the suite rather than only in prose.

    Token overlap cannot match a query to a document that answers it in different words.
    This test asserts the FAILURE, so if a future change adds stemming or synonyms it goes
    red and somebody updates the claim -- rather than the limit quietly ceasing to be true
    while the docstring still says it.
    """
    document = Document(doc_id="t", title="Throttling", body="requests are throttled")
    assert hits("rate limiting", [document]) == [], (
        "token overlap now matches a synonym. That is an improvement, but search.py's "
        "docstring states this limit as a fact -- update it."
    )


def test_render_returns_nothing_for_no_documents_rather_than_a_bare_header():
    """A header with nothing under it reads to a model as "the corpus is empty".

    That is a claim `render` is not entitled to make: only the caller knows whether the
    corpus was empty, absent or switched off, and `provenance.py` is where that is recorded.
    """
    assert render([]) == ""
    text = render([advisories.DOCUMENTS[0]])
    assert "background only" in text, (
        "rendered context must label itself as background; an unlabelled block of retrieved "
        "text reads to a model as instructions"
    )


def test_tokenise_keeps_rule_names_whole():
    """`aws_access_key_id` must survive as one token.

    Splitting on the underscore would make the query `aws-access-key-id` match every
    document that mentions AWS, which is a ranking that looks like it works.
    """
    assert "aws_access_key_id" in tokenise("finding aws_access_key_id in app/auth.py")
    assert "the" not in tokenise("the key"), "a stopword survived"
    assert "os" not in tokenise("os module"), "a two-character token survived"


# ── the corpora themselves ────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(CORPORA))
def test_every_corpus_document_has_a_unique_id_and_a_source(name):
    """Duplicate ids break the tie-break, and a document with no source cannot be checked.

    Provenance that stops at "the conventions corpus" is provenance nobody can verify.
    """
    documents = CORPORA[name]
    ids = [doc.doc_id for doc in documents]
    assert len(ids) == len(set(ids)), f"{name} has duplicate doc_ids: {ids}"
    for doc in documents:
        assert doc.source, f"{name}/{doc.doc_id} has no source, so nothing can be checked"
        assert doc.body.strip(), f"{name}/{doc.doc_id} has an empty body"


def test_the_advisories_corpus_states_no_severity_and_no_false_positive_guidance():
    """H4's constraint, as a test rather than a comment.

    Severity comes from `security/scoring.py`'s ONE table; gitleaks' `critical` is a POLICY
    (any secret-scanner finding is critical). A corpus entry offering a severity would be a
    second declaration of that policy, retrievable by a model, and the two would drift while
    both looked authoritative. "False positive" is worse: it is the exact ammunition the
    boundary suite attacks with, and the corpus must not supply it.
    """
    forbidden = ("false positive", "false-positive", "not blocking", "severity low",
                 "cvss", "does not block", "treat as pass", "exception")
    offenders = []
    for doc in advisories.DOCUMENTS:
        haystack = f"{doc.title} {doc.body} {' '.join(doc.keywords)}".lower()
        for phrase in forbidden:
            if phrase in haystack:
                offenders.append(f"{doc.doc_id}: {phrase!r}")
    assert not offenders, (
        "the advisories corpus contains severity or false-positive language, which would "
        "put a second declaration of the scoring policy where a model can retrieve it:\n  "
        + "\n  ".join(offenders)
    )


def test_the_security_explainer_reads_only_the_advisories_corpus():
    """A channel, not a library. `repo-history` prose beside a finding reads as an argument
    about the verdict even though it cannot be one -- and on a projector, "this was rejected
    before" next to a block invites the reading that the history decided it.
    """
    assert guard.CORPORA["security_explanation"] == (advisories.NAME,)
    assert repo_history.NAME not in guard.CORPORA["security_explanation"]
    assert conventions.NAME not in guard.CORPORA["security_explanation"]


@pytest.mark.parametrize("consumer", sorted(guard.CONSUMERS))
def test_every_declared_consumer_has_corpora_and_can_retrieve(consumer, monkeypatch):
    """A consumer on the allow-list with no corpora would raise `KeyError` at its first call.

    Enumerated from `guard.CONSUMERS` rather than listed, so a consumer added there without
    a corpus assignment fails here. This one is safe to derive from the registry: adding a
    name grows the case list, and the failure mode being guarded against is an
    incompletely-registered consumer rather than a shrinking set.
    """
    monkeypatch.setattr(config, "RETRIEVAL_ENABLED", True)
    assert consumer in guard.CORPORA, f"{consumer} is allowed but has no corpora assigned"
    assert guard.CORPORA[consumer], f"{consumer} has an empty corpus tuple"
    for name in guard.CORPORA[consumer]:
        assert name in guard._LOADERS, f"{consumer} names corpus {name!r} with no loader"
    _, entries, _ = guard.context_for(consumer, "aws credential rate limit python")
    assert len(entries) == len(guard.CORPORA[consumer]), (
        "one provenance entry per corpus, always -- a missing entry is a corpus whose "
        "outcome nothing recorded"
    )
