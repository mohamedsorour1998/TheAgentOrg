"""Render a run's append-only log as a timeline. OWNER: Sorour.

    python -m agentorg.timeline <run_id>            # text timeline
    python -m agentorg.timeline <run_id> --html out.html

The only input is `log.read(run_id)`. No RunState, no state file, no scanner, no
network -- and that constraint is what makes the timeline usable as evidence:
everything on the screen was written down by the pipeline as it ran, so nothing
here can flatter the run it renders. A fact this module cannot get from
`runs/<run_id>.jsonl` is a fact it must not display.

THREE THINGS THIS RENDERS THAT THE RAW ROWS DO NOT SAY

1. WHETHER THE SCANNERS ACTUALLY RAN. `agents/security.run` answers a scanner
   raise with the FIXTURE verdict, which still blocks a diff carrying an AWS
   key, so "blocked" proves two different things and the log row was identical
   either way. `LogEvent.scan_provenance` (week 3) is the field that tells them
   apart; `_PROVENANCE` below turns it into words. Runs logged before that field
   existed carry "" -- most of the corpus, see `_PROVENANCE` -- and those are
   rendered "provenance unknown" -- never
   "scanned", never "fixture", because inferring it is not possible: the block
   fixture's explanation names a real file and a real remediation and reads
   exactly like real gitleaks output.

2. WHETHER THE BLOCK REASON REACHED ANYONE. `github_ops.post_comment` returns
   three different refs and only one of them means "nobody was told". See
   `_delivery`.

3. WHICH WAY THE RUN ENDED, from across a room. See `_OUTCOME` and `_banner`.

Every one of the three is a fact about the run that the spec's row-per-event
rendering left implicit -- present in the bytes, invisible on the screen.
"""

import argparse
import html

from . import log
from .state import LogEvent

# One glyph per terminal action so a run reads at a glance.
_MARK = {
    "opened": "•", "proposed": "→", "reviewed": "✎", "passed": "✓",
    "blocked": "⛔", "approved": "✓", "rejected": "✗", "overridden": "!",
    "merged": "⇄", "promoted": "★",
}

# Provenance, in words a judge can read without knowing the schema. The three
# named values come from state.ScanProvenance; "" is a row written before that
# field existed, and MEASURED on this machine, 1439 of the 1583 security verdict
# rows in runs/ are that shape -- the unknown case is the common one, not a
# curiosity. (Both counts grow with every test run; the shape is the point.)
#
# The unknown case says what is not known rather than nothing at all. A blank
# there would render identically to a real scan, which is the exact conflation
# this field exists to end -- and it is the DEFAULT for every historical run, so
# it is the case most likely to be seen.
_PROVENANCE = {
    "scanners": "real scanners ran",
    "fixture-fallback": "FIXTURE verdict — scanners did not run",
    "fixture-stub": "FIXTURE verdict — scanners not requested",
    "": "provenance unknown — logged before this was recorded",
}

# How a block reason's delivery ref reads. Keys are the ref SCHEMES that
# github_ops.post_comment can return; the values are what the timeline says.
#
# THREE STATES, NOT TWO, and the middle one is why. MEASURED over every
# runs/*.jsonl on this machine, counting the blocked rows that carry a delivery
# ref: local:// 828, comment:// 62, https:// 28 -- 918 rows, so `local://` is
# 828/918 = 90% of them. The RATIO is the durable part of that; the absolute
# counts are not, because `runs/` is gitignored scratch that every `pytest -q`
# adds to, so re-measure rather than trusting these three integers. An earlier
# measurement over a smaller corpus read 196/14/7 = 217 rows, which is the same
# 90%.
#
# 90% is `local://` because the demo runs `OFFLINE=true` and that is the
# documented venue fallback. Treating "not https" as undelivered would therefore
# report ~90% of real blocked runs as "nobody was told", on a projector, when the
# reason was in fact written to the offline NOTES file -- and github_ops returns
# `local://` only AFTER the bytes reach disk, so it is a delivery, not a near
# miss.
_DELIVERY = {
    "https": ("reported", "block reason posted to the PR"),
    "local": ("reported", "block reason written to the offline notes file"),
    "comment": ("NOT REPORTED", "block reason reached nobody — delivery failed"),
}

# A ref whose scheme is none of the three above. github_ops returns only those
# today, so this is reachable exactly when someone adds a fourth shape and does
# not come back here -- and the honest answer then is to say the record was not
# understood, NOT to say nothing. Saying nothing renders identically to a
# blocked row that carried no ref at all, which is the same silent conflation
# this whole module exists to end, so it fails loud instead of quiet.
_DELIVERY_UNRECOGNISED = ("UNRECOGNISED", "delivery ref not understood by this renderer")

# What the whole run amounts to, keyed by the action of its last event. Only the
# actions that actually END a run appear; anything else means the run stopped
# somewhere it does not have a name for, which is the "incomplete" case in
# _outcome() rather than an entry here.
_OUTCOME = {
    "promoted": ("PROMOTED", "★", "the change shipped"),
    "blocked": ("BLOCKED", "⛔", "the change was stopped"),
    "rejected": ("REJECTED", "✗", "a human said no"),
}


def _line(e: LogEvent) -> str:
    mark = _MARK.get(e.action, "•")
    ts = e.ts[11:19]                       # HH:MM:SS from the iso timestamp
    verdict = f" [{e.verdict}]" if e.verdict else ""
    summary = f" — {e.summary}" if e.summary else ""
    return f"{ts} {mark} {e.stage:<8} {e.actor:<9} {e.action}{verdict}{summary}"


def _annotations(e: LogEvent) -> list[str]:
    """The facts about one row that its own text does not state.

    Kept separate from `_line` so the text and HTML renderers cannot disagree
    about what a row means -- the spec gives them two independent format
    strings, which is two places for the same judgement to drift.

    A blocked run writes TWO rows at stage=security -- the agent's own verdict
    row (actor=security) and the system's halt row (actor=system) -- and they get
    ONE annotation each rather than both getting both: the verdict row says what
    was decided and on what evidence, the halt row says who was told. Annotating
    provenance twice read as two unrelated blocks, which is the exact incoherence
    the two-row shape invites.
    """
    notes = []
    if (e.actor, e.stage) == ("security", "security") and e.action in ("blocked", "passed"):
        notes.append(f"scan: {_PROVENANCE.get(e.scan_provenance, _PROVENANCE[''])}")
    delivery = _delivery(e)
    if delivery:
        state, detail = delivery
        notes.append(f"delivery: {state} — {detail}")
    return notes


def _delivery(e: LogEvent) -> tuple[str, str] | None:
    """Classify the block reason's delivery ref on this row, if it has one.

    Reads `artifact_ref` first and falls back to the ref embedded in the summary
    sentence, because BOTH shapes are real and neither is going away: rows
    written from week 3 on carry the field, and every row already on disk carries
    it only inside "pipeline halted by block rule; block reason <ref>".
    A renderer that read one would be blind to the other half of the corpus.

    Scoped to the system/security/blocked row on purpose, NOT to any row
    containing "local://". The PR row's summary is `PR local://agent-org/...`,
    which is a branch, not a delivered block reason -- keying on the scheme
    alone would announce "block reason written to the offline notes file" on
    every clean promoted run that never had a block reason at all.
    """
    if (e.actor, e.stage, e.action) != ("system", "security", "blocked"):
        return None
    ref = e.artifact_ref or _ref_from_summary(e.summary)
    if not ref:
        return None
    return _DELIVERY.get(ref.split("://", 1)[0], _DELIVERY_UNRECOGNISED)


_REF_MARKER = "block reason "


def _ref_from_summary(summary: str) -> str:
    """The delivery ref out of graph.py's halt sentence, or "".

    Anchored on the words that precede the ref rather than on "://" anywhere in
    the string, so a ref is only ever read from the position graph.py writes one.
    """
    _, marker, rest = summary.partition(_REF_MARKER)
    return rest.split()[0] if marker and rest.split() else ""


def _outcome(events: list[LogEvent]) -> tuple[str, str, str]:
    """(label, glyph, detail) for the run as a whole.

    Read off the LAST event's action rather than off any status field, because
    `log.read` is the only input and no row carries `RunState.status`. That is
    also the honest reading: the log is what happened, in order.

    A run whose last action names no ending -- one that died mid-stage, or was
    abandoned at a gate -- gets INCOMPLETE rather than being forced into one of
    the three real outcomes. Reporting an unfinished run as promoted or blocked
    would be the renderer inventing an ending the pipeline never wrote.
    """
    last = events[-1]
    if last.action in _OUTCOME:
        return _OUTCOME[last.action]
    return ("INCOMPLETE", "…", f"run stopped at {last.stage} without an ending")


def _banner(events: list[LogEvent]) -> str:
    """One loud line naming how the run ended.

    THE GLYPHS ALONE ARE NOT ENOUGH, which is why this exists. `⛔` and `★` sit
    in a 24px column at the far left of one row out of fourteen, in a list whose
    other rows also carry `✓` glyphs -- so telling a blocked run from a promoted
    one meant finding the last row and reading it. Across a room, at projector
    distance, that is prose. This is a word, in caps, at the top, above the rows.
    """
    label, glyph, detail = _outcome(events)
    return f"{glyph} {label} — {detail}"


def render_text(run_id: str) -> str:
    events = log.read(run_id)
    if not events:
        return f"(no events for run {run_id})"
    header = f"Timeline for run {run_id} — ticket {events[0].ticket_id}"
    lines = [header, _banner(events)]
    for e in events:
        lines.append(_line(e))
        # Indented under their row rather than appended to it: an annotation is
        # a claim about that event, and the rows are already wide enough that a
        # 60-char suffix would wrap and stop looking like a column.
        lines.extend(f"           ↳ {note}" for note in _annotations(e))
    return "\n".join(lines)


def render_html(run_id: str) -> str:
    events = log.read(run_id)
    rows = "\n".join(
        f"<li class='{_row_class(e)}'><span class='ts'>{html.escape(e.ts[11:19])}</span>"
        f"<span class='mark'>{_MARK.get(e.action, '•')}</span>"
        f"<span class='stage'>{html.escape(e.stage)}</span>"
        f"<span class='actor'>{html.escape(e.actor)}</span>"
        f"<span class='act'>{html.escape(e.action)}"
        f"{(' [' + html.escape(e.verdict) + ']') if e.verdict else ''}</span>"
        f"<span class='sum'>{html.escape(e.summary)}"
        + "".join(f"<em class='note'>{html.escape(n)}</em>" for n in _annotations(e))
        + "</span></li>"
        for e in events)
    tid = html.escape(events[0].ticket_id) if events else run_id
    label, glyph, detail = _outcome(events) if events else ("NO EVENTS", "…", "")
    banner = (
        f"<p class='banner {label.split()[0].lower()}'>{glyph} {html.escape(label)}"
        f"<small>{html.escape(detail)}</small></p>"
    )
    return f"""<!doctype html><meta charset=utf-8>
<title>Timeline {html.escape(run_id)}</title>
<style>
 body{{font:15px/1.5 system-ui;margin:2rem;background:#0d1117;color:#e6edf3}}
 h1{{font-size:1.1rem}} ul{{list-style:none;padding:0}}
 li{{display:grid;grid-template-columns:70px 24px 90px 90px 160px 1fr;
     gap:.5rem;padding:.35rem .5rem;border-left:2px solid #30363d}}
 li:hover{{background:#161b22}} .ts{{color:#8b949e}} .mark{{text-align:center}}
 .stage{{color:#58a6ff}} .actor{{color:#d2a8ff}} .sum{{color:#8b949e}}
 /* The outcome, at projector distance. Colour is NOT the only signal -- the
    word and the glyph carry it too, so this reads on a washed-out projector
    and for a colour-blind viewer. */
 .banner{{font:700 2rem/1.2 system-ui;margin:.25rem 0 1.25rem;padding:.75rem 1rem;
     border-radius:.5rem;border-left:.5rem solid}}
 .banner small{{display:block;font:400 .95rem/1.4 system-ui;opacity:.85}}
 .banner.promoted{{background:#0f2f1a;border-color:#3fb950;color:#7ee787}}
 .banner.blocked{{background:#3a1113;border-color:#f85149;color:#ff9492}}
 .banner.rejected{{background:#3a1113;border-color:#f85149;color:#ff9492}}
 .banner.incomplete{{background:#2b2000;border-color:#d29922;color:#e3b341}}
 /* The row that decided the run, so the eye lands on it without counting. */
 li.blocked{{background:#2d0f11;border-left-color:#f85149}}
 li.promoted{{background:#0d2818;border-left-color:#3fb950}}
 li.rejected{{background:#2d0f11;border-left-color:#f85149}}
 .note{{display:block;font-style:normal;color:#e3b341;font-size:.9em}}
</style>
<h1>Timeline for {html.escape(run_id)} — ticket {tid}</h1>
{banner}
<ul>{rows}</ul>"""


def _row_class(e: LogEvent) -> str:
    """CSS class for one row: its action when that action ends a run, else "".

    Only the ending actions get a class, so the highlight marks the row that
    decided the run rather than every row in the file.
    """
    return e.action if e.action in _OUTCOME else ""


def main() -> None:
    ap = argparse.ArgumentParser(prog="agentorg.timeline")
    ap.add_argument("run_id")
    ap.add_argument("--html", metavar="PATH", help="write an HTML view to PATH")
    args = ap.parse_args()
    if args.html:
        import pathlib
        pathlib.Path(args.html).write_text(render_html(args.run_id))
        print(f"wrote {args.html}")
    else:
        print(render_text(args.run_id))


if __name__ == "__main__":
    main()
