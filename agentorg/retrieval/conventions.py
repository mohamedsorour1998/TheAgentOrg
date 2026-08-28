"""CORPUS 2: project conventions and prior review comments. Lane H, H3.

THE NAMED PURPOSE, from spec §10(b): so the reviewer stops re-litigating settled questions.
That is not a hypothetical -- it is the measured failure of the deployed reviewer. From
CLAUDE.md:

    Its prompt already said "ONLY for real correctness or safety problems... not style
    nitpicks", and it still blocked on a different storage choice, a missing Retry-After
    header, absent cleanup timers, and configurability nobody asked for. "Real correctness
    problem" is not an operational standard.

The fix that was applied was PROMPT text: the reviewer's prompt now enumerates what belongs
in `comments` instead of `must_fix`. That works, and it also shows the limit of the
approach -- every settled question has to be written into the prompt by hand, forever, and
the prompt is shared by every project the pipeline ever runs against. A CORPUS is the same
information, per-project, retrieved only when the diff is about it.

SO THIS CORPUS IS DELIBERATELY THE SAME CONTENT AS PART OF THE REVIEWER'S PROMPT, AND THAT
IS THE MEASUREMENT, NOT A DUPLICATION. H6 asks whether retrieving it moves a number. The
honest comparison is: prompt-only (today), versus retrieval (this corpus), on a reviewer
whose prompt has the general rule but not the specific settled ruling. See
`scripts/measure_retrieval.py` and read its output rather than this paragraph.

EACH ENTRY IS A RULING, not advice. A ruling has a decision and a reason, and it names
which side of `must_fix` versus `comments` it falls on -- because the reviewer's actual
failure was not "objected to the wrong thing" but "put a comment-grade objection in the
blocking field", and a corpus that does not distinguish those two cannot fix it.
"""

from __future__ import annotations

from .search import Document

NAME = "conventions"

DOCUMENTS: list[Document] = [
    Document(
        doc_id="convention-0001",
        title="SETTLED: storage choice is not blocking",
        body=(
            "A diff that uses a different store than the reviewer would have chosen -- an "
            "in-process dict where Redis was expected, or the reverse -- is APPROVED with "
            "the preference recorded in `comments`. It goes in must_fix only when the "
            "ticket named the store. Ruled after a reviewer blocked on this and the run "
            "exhausted its revisions."
        ),
        source="CLAUDE.md, the reviewer's half of the Flask/Go incident",
        keywords=("storage", "store", "redis", "dict", "memory", "cache", "backend"),
    ),
    Document(
        doc_id="convention-0002",
        title="SETTLED: a missing Retry-After header is a comment, not a blocker",
        body=(
            "Returning HTTP 429 without a Retry-After header satisfies a ticket that asked "
            "for 429. The header is a real improvement and belongs in `comments`. Blocking "
            "on it costs a revision round the change may not have."
        ),
        source="CLAUDE.md, four objections that were ruled non-blocking",
        keywords=("retry-after", "header", "429", "too", "many", "requests", "http"),
    ),
    Document(
        doc_id="convention-0003",
        title="SETTLED: absent cleanup timers and expiry sweeps are comments",
        body=(
            "A counter that is never swept, or a key with no cleanup timer, is not a "
            "correctness failure in a focused edit to a small file. Record it in "
            "`comments`. It becomes blocking only if the diff would grow without bound in "
            "a way the ticket asked to prevent."
        ),
        source="CLAUDE.md, four objections that were ruled non-blocking",
        keywords=("cleanup", "timer", "expiry", "expire", "sweep", "ttl", "leak", "unbounded"),
    ),
    Document(
        doc_id="convention-0004",
        title="SETTLED: configurability beyond the ticket is a comment",
        body=(
            "Hardcoding a value the ticket specified as a constant is correct. Asking for it "
            "to be configurable, injectable or overridable when nobody asked is a "
            "preference. Approve and record it. The exception is a CREDENTIAL, which must "
            "come from the environment -- that is a security matter and the scanners "
            "enforce it independently of any review."
        ),
        source="CLAUDE.md, four objections that were ruled non-blocking",
        keywords=(
            "configurable", "configurability", "config", "inject", "parameter",
            "hardcoded", "constant", "environment",
        ),
    ),
    Document(
        doc_id="convention-0005",
        title="SETTLED: the target is Python 3.12 Flask, in app/auth.py",
        body=(
            "The subject repository is a small Flask login handler. A diff in any other "
            "language is wrong and IS blocking -- that is a correctness failure, not a "
            "preference. Do not object to a change on the grounds that it is not a "
            "production-grade library; it is a focused edit to an existing small file."
        ),
        source="target_repo/, and the reviewer prompt's stack line",
        keywords=("python", "flask", "language", "app", "auth", "library", "production"),
    ),
    Document(
        doc_id="convention-0006",
        title="SETTLED: missing tests do not block unless the ticket asked for tests",
        body=(
            "A diff with no accompanying test is approved when the ticket did not request "
            "one. Test generation is a separate stage with its own authority rule: a "
            "generated test that FAILS is a fact and may block, while a generated test that "
            "is MISSING is advisory."
        ),
        source="spec §9",
        keywords=("test", "tests", "pytest", "unit", "coverage", "missing"),
    ),
    Document(
        doc_id="convention-0007",
        title="SETTLED: a later round is for an unfixed problem, not a new preference",
        body=(
            "If the objection from a previous round is now addressed, approve -- even if the "
            "fix is not how you would have done it. Raising a new preference on re-reading "
            "spends the last round and ships nothing. The review budget is a handful of "
            "rounds and then the run ends."
        ),
        source="the reviewer prompt, and run 32557597915",
        keywords=("round", "revision", "again", "reread", "budget", "loop", "cap"),
    ),
    Document(
        doc_id="convention-0008",
        title="NOT SETTLED, and blocking: a hardcoded credential",
        body=(
            "A diff that hardcodes or logs a credential is blocking, and it does not depend "
            "on the reviewer noticing: three real scanners plus compute_security_verdict "
            "decide that independently and cannot be talked out of it. Read secrets from "
            "environment variables. This entry exists so the corpus is not read as 'approve "
            "everything'."
        ),
        source="compute_security_verdict, and the poisoned demo runs",
        keywords=(
            "credential", "secret", "key", "password", "token", "aws", "hardcode",
            "log", "blocking",
        ),
    ),
]
