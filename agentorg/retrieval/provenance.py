"""WHERE RETRIEVED CONTEXT CAME FROM, and whether it came at all. Lane H, task H1.

MODELLED ON `state.ScanProvenance`, deliberately, down to the fourth unnameable value.
That vocabulary exists because "blocked" was produced identically by a real scan and by a
fixture, so the one thing a reader needed to know was the one thing the record could not
say. Retrieval has the same shape and a worse failure mode, because its natural signal is
a COUNT:

    documents == 0

reads identically for three different facts -- the corpus was searched and matched
nothing, nobody asked for retrieval at all, and a corpus raised on load. Those want three
different responses (accept it, turn the knob on, go and fix the corpus), and a count
cannot tell them apart. That is this repository's signature defect: a check that cannot
distinguish "did not run" from "passed".

THE FOUR VALUES, and the one that has no name:

    retrieved     a corpus was searched and returned documents
    empty         a corpus was searched and matched NOTHING -- a fact about the query,
                  not a fault. The corpus is loaded and healthy; this question has no
                  answer in it
    disabled      nobody asked. `config.RETRIEVAL_ENABLED` is false -- a CHOICE
    unavailable   a corpus raised, or its data could not be read -- a FAULT
    ""            a record written before this encoding existed. Rendered as *unknown*,
                  never guessed, exactly as ScanProvenanceOrUnknown's blank is

`empty` AND `unavailable` MUST NOT COLLAPSE, for the reason `fixture-stub` and
`fixture-fallback` must not: one is the system working and the other is the system broken,
and collapsing them hides a broken corpus behind a plausible-looking answer. `disabled`
and `empty` must not collapse either -- a demo that silently retrieved nothing because a
knob was off, reported as "the corpus had nothing to say", is a false negative dressed as
a measurement.

WHY THE VALUE IS ENCODED INTO `RetrievalRecord.corpora` RATHER THAN CARRIED IN A FIELD OF
ITS OWN, AND THIS IS A REAL LIMIT RATHER THAN A PREFERENCE. `agentorg/state.py` is the
frozen contract and `RetrievalRecord` was added by the integrator in a closed batch; it
declares exactly `corpora`, `documents` and `queries`. Adding a fifth field is a contract
change this lane may not make. So the per-corpus outcome travels in the one field whose
meaning it belongs to -- "which corpora were consulted" becomes "which corpora were
consulted, and what each one answered":

    corpora = ["conventions=retrieved", "repo-history=empty", "advisories=unavailable"]

ONE encode function and ONE decode function, because two spellings of an encoding is how
a writer and a reader drift while both keep passing. A bare name with no separator is the
legacy shape and decodes to `""` -- unknown -- rather than being assumed healthy.
"""

from __future__ import annotations

from typing import Literal

# The separator. `=` rather than `:` because a colon reads as a namespace, and a corpus
# name looks enough like a path segment that "repo-history:retrieved" invites being parsed
# as a corpus called `retrieved` inside `repo-history`.
SEPARATOR = "="

RetrievalProvenance = Literal["retrieved", "empty", "disabled", "unavailable"]

RETRIEVED: RetrievalProvenance = "retrieved"
EMPTY: RetrievalProvenance = "empty"
DISABLED: RetrievalProvenance = "disabled"
UNAVAILABLE: RetrievalProvenance = "unavailable"

# The blank fourth state. Not a member of RetrievalProvenance, for the same reason
# `ScanProvenanceOrUnknown` is a separate alias: nothing may WRITE unknown, and a type
# that admits it as a value invites exactly that.
UNKNOWN = ""

VALUES: frozenset[str] = frozenset({RETRIEVED, EMPTY, DISABLED, UNAVAILABLE})

# The two that mean "no documents came back for a reason you should act on". Named here
# rather than re-derived at each call site, because the interesting question a renderer
# asks is almost never "which of the four" but "is this a fault or a choice".
A_FAULT: frozenset[str] = frozenset({UNAVAILABLE})
A_CHOICE: frozenset[str] = frozenset({DISABLED})

_HUMAN = {
    RETRIEVED: "documents were retrieved",
    EMPTY: "the corpus was searched and matched nothing",
    DISABLED: "retrieval was switched off, so nothing was searched",
    UNAVAILABLE: "the corpus could not be read -- this is a fault",
    UNKNOWN: "unknown -- this record predates the provenance encoding",
}


def describe(provenance: str) -> str:
    """One sentence a human can read off a PR comment or a timeline row.

    An unrecognised value is reported AS unrecognised rather than mapped to a default.
    A renderer that quietly prints "unknown" for a value it simply does not know is the
    mislabelled-metric failure: worse than a gap, because it reads as data.
    """
    if provenance in _HUMAN:
        return _HUMAN[provenance]
    return f"unrecognised provenance {provenance!r}"


def encode(corpus: str, provenance: str) -> str:
    """`("conventions", "empty")` -> `"conventions=empty"`. The ONLY writer.

    Refuses an unknown provenance and refuses a corpus name carrying the separator,
    because either one produces an entry that decodes to something else -- and the decode
    would not raise, it would return a plausible wrong answer.
    """
    if provenance not in VALUES:
        raise ValueError(
            f"{provenance!r} is not a retrieval provenance; expected one of "
            f"{sorted(VALUES)}. Nothing may WRITE the blank unknown value."
        )
    if not corpus or SEPARATOR in corpus:
        raise ValueError(
            f"corpus name {corpus!r} is empty or contains {SEPARATOR!r}, so the encoded "
            f"entry would decode to a different corpus"
        )
    return f"{corpus}{SEPARATOR}{provenance}"


def decode(entry: str) -> tuple[str, str]:
    """`"conventions=empty"` -> `("conventions", "empty")`. The ONLY reader.

    A bare name -- `"conventions"` -- is the LEGACY shape, written before this encoding
    existed, and yields `("conventions", "")`: unknown. Not `retrieved`. A record that
    merely names a corpus is not evidence that corpus answered, and reading it as though
    it were is how a run with a dead corpus comes to look like a run with a live one.

    An unrecognised provenance is passed through rather than corrected, so `describe`
    can say it was unrecognised. Silently rewriting it to unknown would lose the fact
    that something wrote a value nobody expected.
    """
    name, separator, provenance = entry.partition(SEPARATOR)
    if not separator:
        return (entry, UNKNOWN)
    return (name, provenance)
