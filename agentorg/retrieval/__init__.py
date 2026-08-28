"""RETRIEVAL, and the one thing it may never touch. Lane H, spec §10, judge requirement 8.

OWNER: Lane H. Owns `agentorg/retrieval/**` and `tests/test_retrieval*.py`. Nothing here
imports a third-party package: every import under `agentorg/` becomes a dependency of all
five arm64 agent containers, asserted by
`tests/test_agentcore_deploy_assets.py::test_requirements_covers_every_third_party_import_in_the_package`.
Three small corpora do not need a vector database, and one that shipped to five containers
to serve them would be the exact failure the spec warns about -- a demo of a vector
database rather than an improvement to the product.

WHAT THIS PACKAGE IS FOR, in one line: give the reviewer, the developer and the security
EXPLAINER context they do not have, without giving any of them a way to move the verdict.

THE BOUNDARY IS THE POINT, and it is enforced three ways rather than stated once:

  1. `RetrievalRecord` declares no field a verdict reads -- `severity`, `verdict`,
     `blocking`, `threshold`, `findings` are all absent, pinned by
     `tests/test_final_contract.py::test_a_retrieval_record_carries_provenance_and_nothing_the_verdict_reads`.
  2. `guard.py` REFUSES at the call boundary: `context_for` raises for any consumer not on
     an allow-list, and the security consumer it does serve is `security_explanation`, not
     `security`. A consumer name is a capability, and there is no name that reaches the
     rule.
  3. `tests/test_retrieval_boundary.py` ATTEMPTS the breach -- documents that argue the
     finding is a false positive, that the threshold should be lower, that the key is a
     test fixture, that the reader is now in maintenance mode -- through the real
     `compute_security_verdict`, and asserts the verdict is unchanged every time.

WHY (2) IS NOT REDUNDANT WITH (1). The contract stops retrieved text from being STORED
where a verdict reads it. It does not stop somebody passing a retrieved string into
`compute_security_verdict`'s `threshold` argument, or into a `Finding.severity`. The guard
is what makes that a raise rather than a `KeyError` inside the one stage whose whole
purpose is to produce a verdict.

WHAT IS *NOT* HERE, stated rather than implied:

  * No embeddings, no vector index, no similarity ranking. Retrieval is deterministic
    token overlap over small documents -- see `search.py` for the measurement that says
    why that is enough here, and for the honest limit.
  * No writes. Nothing in this package creates, mutates or deletes a corpus document.
  * No network. `repo_history` reads a corpus file, not a git remote; conftest guard 6
    exists because `repo_snapshot` shallow-clones the target repository, and this lane is
    the one most likely to reach the network by accident.
"""

from .guard import CONSUMERS, RetrievalBoundaryViolation, context_for
from .provenance import (
    DISABLED,
    EMPTY,
    RETRIEVED,
    UNAVAILABLE,
    UNKNOWN,
    RetrievalProvenance,
)
from .search import Document, hits

__all__ = [
    "CONSUMERS",
    "DISABLED",
    "EMPTY",
    "RETRIEVED",
    "UNAVAILABLE",
    "UNKNOWN",
    "Document",
    "RetrievalBoundaryViolation",
    "RetrievalProvenance",
    "context_for",
    "hits",
]
