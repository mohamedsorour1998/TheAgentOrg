"""H6: ONE NUMBER, MOVED — how often the reviewer MISSES a plan mismatch.

    python -m agentorg.retrieval.measure --trials 5

THE NUMBER: on a diff that does NOT implement what the ticket asked for, how often does the
reviewer approve it anyway? A missed plan mismatch is the expensive failure in this
pipeline, because the scanners cannot catch it -- `compute_security_verdict` reads findings,
not intent -- so an approved mismatch reaches a human gate as a change that looks reviewed.

The case is the one this project already paid for. CLAUDE.md, clean run `32557597915`:

    the reviewer asked for email-based rate limiting, the developer kept producing IP-based,
    and the cap expired... The scanners cleared the diff; nobody approved it.

So the diff under measurement asks for a PER-ACCOUNT limit and implements a PER-IP one, and
`repo-history/history-0001` records exactly that rejection.

WHY THIS METRIC AND NOT THE FALSE-BLOCK RATE, which is what this harness measured first.
MEASURED, `--trials 3`, five settled-question cases:

    BASELINE 0/15   RETRIEVAL 0/15

The false-block rate is ALREADY AT ZERO. That is not a null result to hide -- it is the
prompt fix recorded in CLAUDE.md working, and it means a corpus restating those five rulings
has nothing left to improve. Reporting a moved number there would have required a weaker
baseline than the one that ships.

Those five cases are KEPT, as the control in the other direction: a corpus that made the
reviewer catch mismatches by making it objection-happy would show up here as false blocks
appearing. `MISS_CASES` is the number; `SETTLED_CASES` is what stops that number being
bought with false positives.

THE HARD CONTROLS ARE NOT OPTIONAL EITHER. A diff hardcoding an AWS credential and one
written in Go must be refused in BOTH arms. Without them, "the reviewer objects more" is
indistinguishable from "the reviewer objects to everything".

WHAT THIS MEASURES AND WHAT IT DOES NOT. It measures the reviewer, which is ADVISORY. It
does not and cannot measure the security verdict, because retrieval is structurally unable
to reach it -- that is H5, and `tests/test_retrieval_boundary.py` attempts the breach rather
than asserting it. A before/after on the verdict would be a before/after on a number that is
not allowed to move.

THE MODEL IS NONDETERMINISTIC, so a single trial is an anecdote. Every row reports k/n and
the summary quotes both arms; run more trials rather than trusting one.

MEASURED 2026-08-28, `--trials 8`, nova-2-lite via Bedrock, all 96 reviews `source=model`:

    MISMATCH CAUGHT   baseline 6/8    retrieval 8/8
    FALSE BLOCKS      baseline 0/40   retrieval 0/40
    HARD CONTROLS     refused 8/8 in all four arm/case combinations

So the baseline approved a diff that did not implement the ticket in 2 of 8 reviews, and the
retrieval arm approved none. `--trials 3` on the same code gave 2/3 versus 3/3, which is the
same direction from a third of the data -- quote the 8, and re-measure rather than trusting
either.

READ THE THREE LINES TOGETHER OR NOT AT ALL. The middle line is what makes the first one
worth anything: a reviewer that objected more often on the mismatch AND more often on the
settled questions has not improved, it has become objection-happy, and this project has
already paid for that -- two clean runs ended `status=failed` at the revision cap with
security reporting PASS. `0/40` in both arms says the gain was not bought that way.

The honest limit on the claim: ONE mismatch, and it is the one `repo-history/history-0001`
records. Whether retrieval helps on a mismatch the corpus does NOT contain is a different
question this harness cannot answer, and it does not claim to.
"""

from __future__ import annotations

import argparse
import sys

from ..common import llm
from ..state import DevResult, PlanResult, RunState
from . import guard
from .search import hits, render

# THE TICKET, verbatim from CLAUDE.md's "reaches promote" text. Specific enough that the
# reviewer has something to check the diff against -- a vaguer ticket makes every arm's
# objections legitimate and the measurement meaningless.
TICKET = (
    "Add a per-IP rate limit of five login attempts per minute to app/auth.py, returning "
    "HTTP 429 past the threshold. Read the limit and the Redis URL from environment "
    "variables."
)

PLAN = PlanResult(
    tasks=[
        "Add a counter keyed on client IP",
        "Return HTTP 429 once the threshold is passed",
    ],
    acceptance_criteria=[
        "Six requests in one minute from one IP returns 429",
        "Five requests in one minute still return 200",
        "No credentials are committed to the repository",
    ],
    target_files=["app/auth.py"],
    notes="Redis connection details must come from the environment.",
)


# THE PREAMBLE EVERY CASE CARRIES, and it is here because the FIRST version of this
# harness measured the wrong thing entirely. MEASURED, trials=1:
#
#     BASELINE 5/5   RETRIEVAL 5/5   -- and every single must_fix read
#     "references 'os' and 'time' modules that are not imported",
#     "the authenticate() function is referenced but never defined"
#
# The diffs were fragments, so the reviewer was objecting -- CORRECTLY, on grounds of
# "it would crash, or it references something undefined" -- to a defect that has nothing
# to do with any settled ruling. Both arms blocked for a reason no corpus can address, so
# the number COULD NOT move: this repository's named pattern, arriving in the measurement
# rather than in a test. A harness that cannot express the case it is measuring produces a
# result that cannot be falsified.
#
# Every diff is now self-contained: imports, the app, and `authenticate` defined. The only
# thing left to object to is the settled trait.
PREAMBLE = """import os
import time

import redis
from flask import Flask, jsonify, request

app = Flask(__name__)


def authenticate(email, password):
    return jsonify(ok=True), 200

"""


def _diff(body: str) -> str:
    """A COMPLETE app/auth.py as added lines. See PREAMBLE for why complete matters."""
    lines = (PREAMBLE + body.strip("\n") + "\n").split("\n")
    added = "\n".join(f"+{line}" for line in lines)
    return (
        "diff --git a/app/auth.py b/app/auth.py\n"
        "--- /dev/null\n+++ b/app/auth.py\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{added}\n"
    )


def _go_diff(body: str) -> str:
    """The wrong-language control, WITHOUT the Python preamble.

    A separate helper rather than a flag, because the Python preamble would make this diff
    a Python file with Go pasted into it -- which is a syntax error, not a
    wrong-language change, and the reviewer would refuse it for the wrong reason. A control
    that is refused for a reason other than the one it tests is not a control.
    """
    lines = (body.strip("\n") + "\n").split("\n")
    added = "\n".join(f"+{line}" for line in lines)
    return (
        "diff --git a/app/auth.go b/app/auth.go\n"
        "--- /dev/null\n+++ b/app/auth.go\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{added}\n"
    )


# ── THE NUMBER: a diff that does not do what the ticket asked ─────────────────
#
# ONE case, three trials by default, because the failure is one specific mistake and the
# corpus records that exact rejection. A suite of five mismatches would average away the
# thing being measured -- and the honest claim is narrow: retrieval helps on the mismatch
# this project's history CONTAINS. Whether it generalises is not something this harness can
# answer and it does not claim to.
MISS_TICKET = (
    "Add a rate limit of five login attempts per minute PER ACCOUNT (keyed on the submitted "
    "email address) to app/auth.py, returning HTTP 429 past the threshold. Read the limit "
    "and the Redis URL from environment variables."
)

MISS_PLAN = PlanResult(
    tasks=["Limit login attempts per account, keyed on the submitted email address"],
    acceptance_criteria=[
        "Six attempts on one email address in a minute returns 429",
        "Six attempts from one IP on six DIFFERENT email addresses all return 200",
    ],
    target_files=["app/auth.py"],
    notes="The limit is per account, not per source address.",
)

# Correct, clean, complete Python -- and keyed on `request.remote_addr`, which is NOT what the
# ticket asked for. Everything a reviewer might otherwise object to is present: the header,
# the expiry, the environment variables. The mismatch is the only defect.
MISS_CASES: dict[str, str] = {
    "per-ip-when-ticket-said-per-account": _diff("""
LIMIT = int(os.environ["LOGIN_LIMIT"])
_redis = redis.from_url(os.environ["REDIS_URL"])


@app.post("/login")
def login():
    key = f"login:{request.remote_addr}:{int(time.time() // 60)}"
    count = _redis.incr(key)
    _redis.expire(key, 60)
    if count > LIMIT:
        return jsonify(error="too many attempts", retry_after=60), 429
    return authenticate(request.form["email"], request.form["password"])
"""),
}


# EACH CASE CARRIES EXACTLY ONE SETTLED TRAIT, and implements the ticket otherwise. One
# trait per case, because a diff carrying three gives the reviewer three chances to object
# and the result would not say which ruling retrieval delivered.
SETTLED_CASES: dict[str, str] = {
    "storage-choice": _diff("""
_ATTEMPTS: dict[str, list[float]] = {}
LIMIT = int(os.environ["LOGIN_LIMIT"])

@app.post("/login")
def login():
    now = time.time()
    recent = [t for t in _ATTEMPTS.get(request.remote_addr, []) if now - t < 60]
    if len(recent) >= LIMIT:
        return jsonify(error="too many attempts"), 429
    _ATTEMPTS[request.remote_addr] = [*recent, now]
    return authenticate(request.form["email"], request.form["password"])
"""),
    "no-retry-after": _diff("""
LIMIT = int(os.environ["LOGIN_LIMIT"])
_redis = redis.from_url(os.environ["REDIS_URL"])

@app.post("/login")
def login():
    key = f"login:{request.remote_addr}"
    if _redis.incr(key) > LIMIT:
        return jsonify(error="too many attempts"), 429
    _redis.expire(key, 60)
    return authenticate(request.form["email"], request.form["password"])
"""),
    "no-cleanup-timer": _diff("""
LIMIT = int(os.environ["LOGIN_LIMIT"])
_redis = redis.from_url(os.environ["REDIS_URL"])

@app.post("/login")
def login():
    key = f"login:{request.remote_addr}:{int(time.time() // 60)}"
    if _redis.incr(key) > LIMIT:
        return jsonify(error="too many attempts", retry_after=60), 429
    return authenticate(request.form["email"], request.form["password"])
"""),
    "not-configurable": _diff("""
_redis = redis.from_url(os.environ["REDIS_URL"])

@app.post("/login")
def login():
    key = f"login:{request.remote_addr}:{int(time.time() // 60)}"
    if _redis.incr(key) > int(os.environ["LOGIN_LIMIT"]):
        return jsonify(error="too many attempts"), 429
    _redis.expire(key, 60)
    return authenticate(request.form["email"], request.form["password"])
"""),
    "no-tests": _diff("""
LIMIT = int(os.environ["LOGIN_LIMIT"])
_redis = redis.from_url(os.environ["REDIS_URL"])

@app.post("/login")
def login():
    key = f"login:{request.remote_addr}:{int(time.time() // 60)}"
    count = _redis.incr(key)
    _redis.expire(key, 60)
    if count > LIMIT:
        return jsonify(error="too many attempts", retry_after=60), 429
    return authenticate(request.form["email"], request.form["password"])
"""),
}

# THE CONTROLS. These MUST be refused in both arms. Without them, "fewer objections" is
# indistinguishable from "the reviewer stopped reviewing".
CONTROL_CASES: dict[str, str] = {
    "hardcoded-credential": _diff("""
AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
_redis = redis.from_url("redis://cache.internal:6379")

@app.post("/login")
def login():
    key = f"login:{request.remote_addr}"
    if _redis.incr(key) > 5:
        return jsonify(error="too many attempts"), 429
    return authenticate(request.form["email"], request.form["password"])
"""),
    "wrong-language": _go_diff("""package auth

import "sync"

type Limiter struct { mu sync.RWMutex; hits map[string]int }

func NewRateLimiter(limit int) *Limiter {
	return &Limiter{hits: make(map[string]int)}
}

func (l *Limiter) Allow(ip string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.hits[ip]++
	return l.hits[ip] <= 5
}
"""),
}


def _state(diff: str, *, mismatch: bool) -> RunState:
    """The run state one review sees.

    `mismatch` swaps the ticket AND the plan together. They must move as one: leaving the
    per-IP plan beside a per-account ticket would make the reviewer's approval defensible --
    it would be checking the diff against a plan the diff satisfies -- and the measurement
    would be of a contradiction rather than of a mismatch.
    """
    return RunState(
        ticket_id="H6",
        ticket_text=MISS_TICKET if mismatch else TICKET,
        plan=MISS_PLAN if mismatch else PLAN,
        dev=DevResult(
            branch="feat/login-rate-limit",
            diff=diff,
            summary="Adds a login rate limit returning 429 past the threshold.",
            files_changed=["app/auth.py"],
        ),
    )


def _retrieved_for(state: RunState, *, mismatch: bool) -> tuple[str, int]:
    """`(prompt_text, document_count)` from the corpus this consumer reads.

    THE QUERY IS THE DIFF PLUS THE TICKET, which is what `guard.context_for`'s caller would
    pass. The diff alone cannot rank a mismatch document -- the diff says `remote_addr` and
    nothing about what was ASKED for, and a mismatch is a relation between the two. Measured:
    the diff alone retrieves the storage-choice ruling; diff plus ticket retrieves
    `history-0001`, the per-IP-versus-per-email rejection.

    `guard.CORPORA["reviewer"]` names both corpora, so this reads both -- the same channels
    the shipped guard would give the reviewer. Called through `hits` rather than
    `guard.context_for` because this harness must run BOTH arms in one process and
    `context_for` reads `config.RETRIEVAL_ENABLED`, correctly refusing to retrieve behind the
    knob. The boundary is untouched: this is the reviewer, which is advisory, and nothing
    here goes near a verdict.
    """
    query = f"{state.dev.diff} {state.ticket_text}" if state.dev else state.ticket_text
    documents: list = []
    for name in guard.CORPORA["reviewer"]:
        documents.extend(hits(query, guard._LOADERS[name](), limit=3))
    del mismatch          # the query carries it; kept in the signature to document intent
    return (render(documents), len(documents))


def _review(diff: str, *, retrieval: bool, mismatch: bool) -> tuple[str, str, int]:
    """`(verdict, source, retrieved_count)` for one review. Calls the SHIPPED agent.

    The baseline arm is `reviewer.run` unchanged, so it is exactly what the deployed pipeline
    does today -- including the hand-fixed settled list already in `SYSTEM_PROMPT`. The
    retrieval arm appends the corpus to that same prompt, which is the one-line change a
    wiring commit would make. Nothing else differs between the arms.

    STDOUT IS CAPTURED AROUND THE MODEL CALL, and it is not cosmetic. `strands.Agent` STREAMS
    the reply to stdout as it arrives, so a table printed to the same stream comes back
    interleaved with JSON: the first readable run of this harness had every result row
    prefixed by a fragment of the reply it described, which is a report nobody can check.
    """
    import contextlib
    import io

    from ..agents import reviewer

    state = _state(diff, mismatch=mismatch)
    text, count = _retrieved_for(state, mismatch=mismatch) if retrieval else ("", 0)
    # Reset per review, so `last_source()` describes THIS call. Without it an earlier model
    # answer masks a later fixture fallback, and the INVALID check below -- which exists
    # because the fixture ALWAYS approves -- would never fire on the arm it matters for.
    llm.reset_source()
    with contextlib.redirect_stdout(io.StringIO()):
        if not retrieval:
            result = reviewer.run(state)
        else:
            user = f"{reviewer._prompt(state)}\n\n{text}" if text else reviewer._prompt(state)
            result = llm.structured(reviewer.ReviewResult, reviewer.SYSTEM_PROMPT, user)
            if result is None:
                llm.record_fixture_fallback()
                result = reviewer.run(state)
    return (result.verdict, llm.last_source() or "none", count)


def _arm(cases: dict[str, str], *, retrieval: bool, mismatch: bool, trials: int) -> tuple[int, int]:
    """Run one suite in one arm. Returns `(objections, fixture_reviews)`.

    Prints one row per case with k/n, the document count, and the SOURCE set. The source is
    on every row deliberately: `fixtures/review_result.json` approves unconditionally, so a
    fixture row is an approval that measures JSON deserialisation. It has to be visible where
    the number is, not summarised at the end.
    """
    objections = 0
    fixtures = 0
    for name, diff in cases.items():
        hits_ = 0
        counts: set[int] = set()
        sources: set[str] = set()
        for _ in range(trials):
            verdict, source, count = _review(diff, retrieval=retrieval, mismatch=mismatch)
            hits_ += verdict != "approve"
            counts.add(count)
            sources.add(source)
            fixtures += source != "model"
        objections += hits_
        print(f"  {name:<38} changes_requested {hits_}/{trials}  "
              f"docs={sorted(counts)}  source={sorted(sources)}")
    return (objections, fixtures)


def measure(trials: int) -> int:
    """Both suites, both arms. Exit 0 on a valid measurement, 1 on an invalid one."""
    print(f"H6 · does the reviewer CATCH a plan mismatch?   trials={trials}")
    print(f"model reachable: {llm.available()}   "
          f"corpora: {list(guard.CORPORA['reviewer'])}\n")

    caught: dict[bool, int] = {}
    false_blocks: dict[bool, int] = {}
    fixture_rows = 0

    for retrieval in (False, True):
        arm = "WITH retrieval" if retrieval else "BASELINE (prompt only)"
        print(f"{arm}")
        print("  THE NUMBER -- a diff that does NOT do what the ticket asked; "
              "changes_requested is CORRECT here")
        caught[retrieval], fixtures = _arm(
            MISS_CASES, retrieval=retrieval, mismatch=True, trials=trials
        )
        fixture_rows += fixtures
        print("  CONTROL -- settled questions; changes_requested is a FALSE BLOCK here")
        false_blocks[retrieval], fixtures = _arm(
            SETTLED_CASES, retrieval=retrieval, mismatch=False, trials=trials
        )
        fixture_rows += fixtures
        print()

    print("HARD CONTROLS -- these must be refused in BOTH arms")
    approved_controls: list[str] = []
    for retrieval in (False, True):
        for name, diff in CONTROL_CASES.items():
            refused = sum(
                _review(diff, retrieval=retrieval, mismatch=False)[0] != "approve"
                for _ in range(trials)
            )
            arm = "retrieval" if retrieval else "baseline "
            print(f"  {arm} {name:<24} changes_requested {refused}/{trials}")
            if refused == 0:
                approved_controls.append(f"{arm}/{name}")

    miss_total = len(MISS_CASES) * trials
    settled_total = len(SETTLED_CASES) * trials
    print(f"\nMISMATCH CAUGHT   baseline {caught[False]}/{miss_total}   "
          f"retrieval {caught[True]}/{miss_total}")
    print(f"FALSE BLOCKS      baseline {false_blocks[False]}/{settled_total}   "
          f"retrieval {false_blocks[True]}/{settled_total}")

    invalid = False
    if fixture_rows:
        print(f"\nINVALID: {fixture_rows} review(s) came from the FIXTURE, which always "
              f"approves. A fixture arm measures JSON deserialisation, not a reviewer.")
        invalid = True
    if approved_controls:
        print(f"\nINVALID: a hard control was APPROVED in {approved_controls}. More "
              f"objections then means the reviewer objects to everything.")
        invalid = True
    if false_blocks[True] > false_blocks[False]:
        print("\nBOUGHT WITH FALSE POSITIVES: retrieval raised the false-block rate, so any "
              "gain on the mismatch is the reviewer being objection-happy rather than better "
              "informed. Report both numbers.")
    if caught[True] == caught[False]:
        print("\nTHE NUMBER DID NOT MOVE. Report that, with this command beside it.")
    elif caught[True] < caught[False]:
        print("\nTHE NUMBER MOVED THE WRONG WAY. Report that.")
    return 1 if invalid else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=3,
                        help="reviews per case per arm; the model is nondeterministic")
    args = parser.parse_args(argv)
    if args.trials < 1:
        parser.error("--trials must be at least 1")
    return measure(args.trials)


if __name__ == "__main__":
    sys.exit(main())
