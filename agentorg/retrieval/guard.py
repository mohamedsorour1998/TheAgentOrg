"""THE HARD BOUNDARY: retrieval may never reach the gate. Lane H, task H5.

AN ASSERTION IN CODE, NOT A COMMENT -- the implementation plan says so in those words, and
this file is that assertion. The rule: retrieved text is context for PROSE and DRAFTING. It
is never an input to the severity decision. A poisoned document that could reach the
verdict would be a way to argue past the threshold, which is exactly the attack
`compute_security_verdict` exists to prevent: five lines of Python, no model, so nothing in
the decision can be persuaded.

WHY A CONTRACT WITHOUT FIELDS IS NOT ENOUGH, and this is the whole reason the file exists.
`RetrievalRecord` declares no field a verdict reads, which stops retrieved text being
STORED where the rule looks. It does not stop code from PASSING a retrieved string into
`compute_security_verdict(threshold=...)`, or into `Finding.severity`, or into
`config.SECURITY_BLOCK_THRESHOLD`. Those are the reachable attacks and they are all one
plausible-looking line. Measured on the shipped rule before this guard existed:

    compute_security_verdict([], threshold="HIGH")  ->  KeyError: 'HIGH'

raised from inside the one stage whose whole purpose is to produce a verdict, with a
traceback naming a dict lookup rather than a poisoned input. A retrieved string reaching
that argument would be indistinguishable from a misconfigured knob.

THE MECHANISM: A CONSUMER NAME IS A CAPABILITY. `context_for` takes the name of the thing
asking, refuses any name not on `CONSUMERS`, and there is NO NAME THAT REACHES THE RULE.
The security consumer is spelled `security_explanation`, not `security`, and that spelling
is the design: `SecurityResult.explanation` is prose the model writes AFTER the verdict is
computed, and `agents/security.py` never parses it. So the one place retrieval touches the
security stage is a field that is written after the decision and read by nobody but a human.

WHY AN ALLOW-LIST AND NOT A DENY-LIST. A deny-list is wrong the day somebody adds a
consumer: a new caller gets access by default and the refusal has to be remembered. An
allow-list fails closed -- a new consumer raises until it is named here, and naming it is
where the argument about whether it may have retrieved text happens. Same direction as
`budgets.check` refusing a tenant with no budget row, and `host()` refusing a
registered-but-unshipped adapter.

WHAT THIS GUARD DOES NOT CLAIM. It cannot stop a caller who never calls it -- a module that
imports a corpus directly and hands a document body to the rule bypasses this file
entirely. That is why `tests/test_retrieval_boundary.py` does not test the guard alone: it
drives the REAL `compute_security_verdict` with hostile retrieved text at every argument it
accepts, and asserts the verdict is unchanged. A guard verified only through itself is a
guard verified nowhere.
"""

from __future__ import annotations

import logging
from typing import Literal

from ..common import config
from . import advisories, conventions, provenance, repo_history
from .search import Document, hits, render

# WHO MAY ASK, and what each is allowed to do with the answer. This mapping is the
# capability list; a name absent from it raises.
#
# `security_explanation` is deliberately NOT `security`. The security stage has two halves
# and only one of them may see retrieved text:
#
#     compute_security_verdict(findings, threshold)   <- the DECISION. No retrieval, ever.
#     _explain(verdict, blocking) -> explanation      <- PROSE, written after the decision
#
# The verdict is already computed and passed in when `_explain` runs, and its reply is only
# ever assigned to `SecurityResult.explanation`. So there is no name here that any code path
# reaching the rule could legitimately use, and a caller inside the rule has nothing to pass.
Consumer = Literal["reviewer", "developer", "planner", "security_explanation"]

CONSUMERS: dict[str, str] = {
    "reviewer": "prose and prior-objection context; the verdict stays advisory either way",
    "developer": "drafting context -- why a past attempt at this change was refused",
    "planner": "drafting context for the task list",
    "security_explanation": (
        "PROSE ONLY. Reaches SecurityResult.explanation, which is written after "
        "compute_security_verdict has already decided and is never parsed."
    ),
}

# WHICH CORPUS EACH CONSUMER READS. Per-consumer rather than "all corpora for everyone",
# because a corpus is a channel: giving the security explainer the repo-history corpus
# would put "this was rejected before" prose beside a finding, which reads like an argument
# about the verdict even though it cannot be one.
CORPORA: dict[str, tuple[str, ...]] = {
    "reviewer": (conventions.NAME, repo_history.NAME),
    "developer": (repo_history.NAME, conventions.NAME),
    "planner": (repo_history.NAME,),
    "security_explanation": (advisories.NAME,),
}

_LOADERS = {
    repo_history.NAME: lambda: repo_history.DOCUMENTS,
    conventions.NAME: lambda: conventions.DOCUMENTS,
    advisories.NAME: lambda: advisories.DOCUMENTS,
}

# Names a verdict reads. Held here as data so the refusal below is checkable rather than
# implied, and so it is the SAME set `tests/test_final_contract.py` asserts is absent from
# `RetrievalRecord` -- two declarations of one fact would drift, but this one is about
# ARGUMENT names and that one is about FIELD names, which is a different check on purpose.
VERDICT_ARGUMENTS: frozenset[str] = frozenset({
    "threshold", "severity", "verdict", "blocking", "findings", "cutoff",
})


class RetrievalBoundaryViolation(RuntimeError):
    """Retrieved text was asked for by, or on behalf of, something that decides.

    A `RuntimeError` rather than a `ValueError`: this is not a bad value, it is a call that
    must not exist. It is deliberately NOT caught anywhere in this package -- an absorbed
    boundary violation is a boundary that is not one, and `agents/security.py`'s broad
    `except Exception` is exactly the shape that would absorb it, which is why nothing here
    is called from inside the rule.
    """


def refuse_verdict_arguments(**kwargs: object) -> None:
    """Refuse if any keyword names something a verdict reads. The reachable attack.

    Called by any future code path that forwards retrieved values onward. It exists because
    the dangerous line does not look dangerous:

        compute_security_verdict(findings, threshold=retrieved_threshold)

    reads exactly like correct code, would raise `KeyError` deep inside the security stage,
    and the traceback would name a dict lookup rather than the retrieved document that
    caused it.
    """
    named = VERDICT_ARGUMENTS & set(kwargs)
    if named:
        raise RetrievalBoundaryViolation(
            f"retrieved context was passed as {sorted(named)}, which the security verdict "
            f"reads. Retrieved text is context for prose and drafting only; if it could "
            f"reach the verdict, a poisoned document would be a way to argue past the "
            f"threshold."
        )


def context_for(
    consumer: str,
    query: str,
    limit: int = 3,
) -> tuple[str, list[str], int]:
    """`(prompt_text, corpora_provenance_entries, document_count)`.

    THE RETURN SHAPE IS THE PROVENANCE CONTRACT. A caller cannot obtain the text without
    also obtaining the per-corpus provenance entries and the count -- so a stage that puts
    retrieved text into a prompt and records nothing has to actively discard the record
    rather than merely forget it. The three values go straight into `RetrievalRecord`.

    Every corpus reports one of four outcomes and they are kept apart:

        retrieved     documents came back
        empty         searched, matched nothing -- a fact about the query
        disabled      RETRIEVAL_ENABLED is false -- a CHOICE
        unavailable   the corpus raised -- a FAULT

    `documents == 0` reads identically for the last three, which is why the count is never
    the provenance. Read `config.RETRIEVAL_ENABLED` through the module, never as a bare
    imported name: the bare form binds at import, before any fixture runs, and the knob
    would silently ignore both the tests and the deployed environment.
    """
    if consumer not in CONSUMERS:
        raise RetrievalBoundaryViolation(
            f"{consumer!r} is not a retrieval consumer. Allowed: {sorted(CONSUMERS)}. "
            f"An allow-list rather than a deny-list, so a new consumer must be argued for "
            f"here rather than getting retrieved text by default. Note there is no consumer "
            f"name that reaches compute_security_verdict: the security stage's entry is "
            f"'security_explanation', which is prose written after the verdict is decided."
        )

    names = CORPORA[consumer]
    if not config.RETRIEVAL_ENABLED:
        # A CHOICE, not a fault, and not the same as an empty result. Recorded per corpus
        # so a reader sees which corpora WOULD have been consulted -- "disabled" against a
        # named corpus is a different fact from a blank record.
        return ("", [provenance.encode(n, provenance.DISABLED) for n in names], 0)

    entries: list[str] = []
    found: list[Document] = []
    for name in names:
        try:
            documents = _LOADERS[name]()
            matched = hits(query, documents, limit=limit)
        except Exception as exc:
            # BROAD DELIBERATELY, and it does not weaken the boundary. A corpus is data:
            # its failure surface is whatever a future loader does -- a missing file, a
            # decode error, a malformed entry -- and a narrow clause would be correct only
            # on the day it is written. Critically this catches a corpus LOAD failure, not
            # a boundary violation: `RetrievalBoundaryViolation` is raised by
            # `context_for` above this loop and by `refuse_verdict_arguments`, neither of
            # which runs inside it, so no refusal can be absorbed here.
            #
            # Logger fetched inline: ruff's BLE001 is satisfied only by a logging call it
            # can statically resolve, and it cannot resolve a module-level alias.
            logging.getLogger(__name__).warning(
                "corpus %r could not be read (%s); recording it as unavailable rather "
                "than as an empty result",
                name,
                type(exc).__name__,
            )
            logging.getLogger(__name__).debug("corpus failure traceback", exc_info=True)
            entries.append(provenance.encode(name, provenance.UNAVAILABLE))
            continue
        entries.append(provenance.encode(
            name, provenance.RETRIEVED if matched else provenance.EMPTY
        ))
        found.extend(matched)

    return (render(found), entries, len(found))
