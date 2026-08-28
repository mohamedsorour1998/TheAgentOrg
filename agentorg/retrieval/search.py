"""RANKING, deterministically, with no vector database. Lane H.

WHY NOT EMBEDDINGS, measured rather than asserted. The three corpora in this package hold
tens of documents of a few hundred words each, and the queries are drawn from a ticket
title, a plan's task list and a finding's rule name -- so the vocabulary overlap between a
query and its right answer is high and literal. A vector index earns its place when the
query and the document share MEANING but not WORDS; here they mostly share words.

The cost of the alternative is not hypothetical. Any embeddings client or vector store
imported from `agentorg/` becomes a pinned dependency of all five arm64 AgentCore
containers -- `tests/test_agentcore_deploy_assets.py` AST-walks this package and asserts
every third-party import appears in `agents/requirements.txt`. Lane K measured that same
test refusing `starlette`, which is already INSTALLED, for exactly this reason. So the
trade is: one more thing that can fail at runtime on arm64, in five images, against a
ranking improvement on documents short enough to read whole.

THE HONEST LIMIT, and it is a real one: this cannot match a query to a document that
answers it in different words. A query "rate limiting" does not reach a document that only
says "throttle". Mitigated deliberately rather than hidden -- each corpus document carries
explicit `keywords`, which is where a synonym goes, and `test_retrieval_search.py` pins a
case that FAILS to match so the limit is visible in the suite rather than only in prose.

SCORING, and why every clause is here:

  * OVERLAP OF TOKEN SETS, not counts. A document repeating "redis" nine times is not nine
    times more relevant, and term frequency on documents this short mostly measures
    verbosity.
  * `keywords` WEIGH MORE THAN BODY TEXT. A word the corpus author chose as the document's
    handle is stronger evidence than the same word appearing in a sentence.
  * TIES BREAK ON DOCUMENT ID, always. A ranking whose order depends on dict insertion is
    a ranking that changes when a corpus file is re-saved, and then "retrieval improved the
    reviewer" becomes unreproducible. Determinism here is not decoration -- H6's whole
    claim is a before/after, and a before/after over a nondeterministic ranker measures
    the ranker's mood.
  * ZERO OVERLAP IS DROPPED, never returned with score 0. A document that matched nothing
    is not a weak hit; including it makes `documents` in the provenance record count
    documents nobody retrieved.

STOPWORDS ARE DELIBERATELY SMALL. A long list is a second place where meaning lives, and
"a change was NOT approved" turns on words a big list removes. This one holds only tokens
that appear in nearly every English sentence and carry no retrieval signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Words too common to discriminate. Kept short on purpose -- see the module docstring.
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it",
    "of", "on", "or", "that", "the", "this", "to", "was", "were", "with",
})

# How much more a curated keyword counts than the same word in the body. 3, not 10: a
# keyword should win a close call, not let a document with one lucky keyword beat a
# document that genuinely discusses the query.
KEYWORD_WEIGHT = 3

_TOKEN = re.compile(r"[a-z0-9_]+")

# Minimum token length. One- and two-character tokens are noise here ("if", "os", "5"),
# and dropping them costs nothing the corpora depend on.
MIN_TOKEN_LENGTH = 3


def tokenise(text: str) -> frozenset[str]:
    """Lower-case alphanumeric tokens, stopwords and short tokens removed.

    A SET, not a list, because scoring is overlap rather than frequency. Underscores are
    kept inside tokens so `aws_access_key_id` survives as one token -- the finding rule
    names this ranks against are spelled that way, and splitting them would make the
    query `aws-access-key-id` match every document mentioning AWS.
    """
    return frozenset(
        token
        for token in _TOKEN.findall(text.lower())
        if len(token) >= MIN_TOKEN_LENGTH and token not in STOPWORDS
    )


@dataclass(frozen=True)
class Document:
    """One retrievable document. Frozen: a corpus entry is never edited in place.

    `doc_id` is the tie-break key and appears in the rendered context, so it must be
    stable and meaningful to a human reading a PR comment. `source` says where the fact
    came from -- a PR number, a file path, an advisory id -- because provenance that stops
    at "the conventions corpus" cannot be checked by anybody.
    """

    doc_id: str
    title: str
    body: str
    source: str = ""
    keywords: tuple[str, ...] = field(default_factory=tuple)

    def score(self, query_tokens: frozenset[str]) -> int:
        """Weighted overlap. Deterministic, integer, no floats.

        Integers rather than a normalised float because a normalised score invites being
        compared against a threshold, and a relevance threshold is a knob nobody can set
        honestly on a corpus of thirty documents. The consumer takes the top `limit`.
        """
        body_tokens = tokenise(f"{self.title} {self.body}")
        keyword_tokens = tokenise(" ".join(self.keywords))
        return (
            len(query_tokens & body_tokens)
            + KEYWORD_WEIGHT * len(query_tokens & keyword_tokens)
        )


def hits(query: str, documents: list[Document], limit: int = 3) -> list[Document]:
    """The top `limit` documents with non-zero overlap, best first, ties by `doc_id`.

    Returns `[]` for an empty query rather than the first `limit` documents. A blank
    query retrieving three arbitrary documents is worse than retrieving nothing: it puts
    unrelated text in a prompt and records `provenance=retrieved`, so the record claims a
    successful retrieval for a question nobody asked.
    """
    if limit <= 0:
        raise ValueError(f"limit must be positive; got {limit}")
    query_tokens = tokenise(query)
    if not query_tokens:
        return []
    scored = [(doc.score(query_tokens), doc) for doc in documents]
    ranked = sorted(
        ((score, doc) for score, doc in scored if score > 0),
        key=lambda pair: (-pair[0], pair[1].doc_id),
    )
    return [doc for _, doc in ranked[:limit]]


def render(documents: list[Document]) -> str:
    """The block that goes into a prompt. `""` for no documents, never a header alone.

    A header with nothing under it reads to a model as "the corpus was consulted and is
    empty", which is a claim this function is not entitled to make -- the caller knows
    whether the corpus was empty or absent, and `provenance.py` is where that is recorded.
    """
    if not documents:
        return ""
    lines = ["RETRIEVED CONTEXT (background only -- it decides nothing):"]
    for doc in documents:
        origin = f" [{doc.source}]" if doc.source else ""
        lines.append(f"- {doc.title}{origin}: {doc.body}")
    return "\n".join(lines)
