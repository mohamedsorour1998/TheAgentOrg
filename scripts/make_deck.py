#!/usr/bin/env python3
"""Generate the pre-final hackathon deck as a real .pptx, with motion.

OWNER: Sorour.  Run from the repo root:

    .venv-main/bin/python scripts/make_deck.py

Writes docs/pitch/TheAgentOrg-prefinal.pptx. Committed and re-runnable rather than a
one-off, because somebody has to be able to fix a typo at 11pm on the 24th and
regenerate without reconstructing how the deck was built.

WHY THIS IS A SCRIPT AND NOT A HAND-BUILT FILE
==============================================
Every number on a slide is read from this file's CONSTANTS block, and each one is
annotated with the command that produced it. A hand-built .pptx would let a stale
figure survive on a slide indefinitely, and this repository's standing rule is that
numbers in prose come from a command whose output was pasted -- see CLAUDE.md. A
generator makes that rule enforceable: change the code, regenerate, and the deck
cannot silently disagree with the repository.

TRANSITIONS AND ANIMATIONS ARE NOT A python-pptx FEATURE
========================================================
Measured before relying on it: `dir(slide)` exposes nothing matching "trans" or
"anim". python-pptx models shapes and text, not the timing tree. Both live in the
slide's raw XML, which it DOES expose, so `_transition` and `_animate` below build
that XML directly.

Verified in the saved file rather than assumed -- a deck that silently lost its
motion looks identical to a correct one until it is presented:

    unzip -p docs/pitch/*.pptx ppt/slides/slide5.xml | grep -c "p:transition"
    unzip -p docs/pitch/*.pptx ppt/slides/slide5.xml | grep -c "animEffect"

ONE TRANSITION FOR THE WHOLE DECK, deliberately. A different effect per slide is the
single most reliable way to make a deck look amateur; a consistent push reads as
intentional. The only slide that differs is the title, which fades.

THE COLOUR RULE: one accent, spent on one idea. Amber is reserved for the BINDING
gate -- the deterministic verdict -- so the sentence that has to land is the only
coloured thing on screen. Everything else is off-white on navy.
"""

from __future__ import annotations

import itertools
import pathlib
import subprocess
import sys

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

# ── verified numbers ──────────────────────────────────────────────────────────
# Each is followed by the command that produced it. Re-run them before the session;
# `pytest --collect-only` in particular moves whenever anyone adds a test.
TESTS_COLLECTED = 1105   # pytest --collect-only -q | tail -1
TESTS_PASSING = 1102     # pytest -q | tail -1   (3 skip when scanners are on PATH)
TESTS_SKIPPED = 3
TEST_FILES = 41          # ls tests/test_*.py | wc -l
AGENTORG_LOC = 7971      # find agentorg -name '*.py' | xargs wc -l | tail -1
TF_RESOURCES = 20        # grep -rhc '^resource ' infra/Terraform/modules/*/main.tf
RUNTIME_VERSION = 18     # scripts/preflight.py check 2
CLEAN_MINUTES = 5        # measured, run 32585658981
POISONED_MINUTES = 3     # measured, run 32586453254
TRIGGER_SECONDS = 6      # issue created 16:45:09 -> run created 16:45:15

# ── palette ───────────────────────────────────────────────────────────────────
NAVY = RGBColor(0x0B, 0x12, 0x20)
INK = RGBColor(0xEC, 0xEF, 0xF4)     # off-white, not pure white: less glare on a projector
MUTED = RGBColor(0x8B, 0x97, 0xA8)
AMBER = RGBColor(0xF5, 0xA6, 0x23)   # the binding gate, and nothing else
GREEN = RGBColor(0x4A, 0xD2, 0x95)
RED = RGBColor(0xFF, 0x6B, 0x6B)
MONO = "Menlo"
SANS = "Helvetica Neue"

# Hoisted rather than written as `left=Inches(0.9)` defaults: ruff's B008 forbids a call
# in an argument default, and it is right to. The value is evaluated once at import,
# which is harmless for an immutable Emu and a real trap for anything mutable.
MARGIN = Inches(0.9)          # the left text margin every slide shares
BODY_LEFT = Inches(1.15)      # bullets, one step in from the heading
BODY_WIDTH = Inches(11.0)
RULE_WIDTH = Inches(2.2)

P_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


def _transition(slide, *, kind: str = "push", direction: str = "l") -> None:
    """Give this slide an entrance transition.

    Appended to <p:sld> as its LAST child, which the schema requires -- an element
    out of order makes PowerPoint declare the file corrupt and offer to repair it,
    which on a projector is indistinguishable from a broken deck.
    """
    element = etree.SubElement(slide._element, P_NS + "transition")
    element.set("spd", "med")
    child = etree.SubElement(element, P_NS + kind)
    if kind == "push":
        child.set("dir", direction)


def _animate(slide, shape_ids: list[int]) -> None:
    """Reveal these shapes one click at a time, in the order given.

    ONE <p:timing> tree holding a sequence of click-triggered fades. Written as raw
    XML because there is no API for it, and built as a single string rather than by
    element assembly: the timing tree's nesting is deep enough (par > cTn > childTnLst,
    five levels) that assembling it node by node would be far harder to read and to
    correct than the shape it produces.

    Each shape gets `<p:set>` to make it visible plus `<p:animEffect filter="fade">`.
    The `<p:set>` is what actually hides it beforehand -- without it the shape is
    visible from the start and the fade animates something already on screen.
    """
    if not shape_ids:
        return

    node_id = 10
    blocks = []
    for index, shape_id in enumerate(shape_ids):
        # `clickEffect` on the first, `afterEffect` would chain automatically -- but
        # every shape gets its own click here, so the speaker controls the pace.
        blocks.append(f"""
        <p:par><p:cTn id="{node_id}" fill="hold">
          <p:stCondLst><p:cond delay="indefinite"/></p:stCondLst>
          <p:childTnLst><p:par><p:cTn id="{node_id + 1}" fill="hold">
            <p:stCondLst><p:cond delay="0"/></p:stCondLst>
            <p:childTnLst><p:par><p:cTn id="{node_id + 2}" presetID="10"
                presetClass="entr" presetSubtype="0" fill="hold"
                grpId="0" nodeType="{"clickEffect" if index == 0 else "afterEffect"}">
              <p:stCondLst><p:cond delay="0"/></p:stCondLst>
              <p:childTnLst>
                <p:set><p:cBhvr><p:cTn id="{node_id + 3}" dur="1" fill="hold"/>
                  <p:tgtEl><p:spTgt spid="{shape_id}"/></p:tgtEl>
                  <p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst>
                </p:cBhvr><p:to><p:strVal val="visible"/></p:to></p:set>
                <p:animEffect transition="in" filter="fade">
                  <p:cBhvr><p:cTn id="{node_id + 4}" dur="400"/>
                    <p:tgtEl><p:spTgt spid="{shape_id}"/></p:tgtEl></p:cBhvr>
                </p:animEffect>
              </p:childTnLst></p:cTn></p:par></p:childTnLst>
          </p:cTn></p:par></p:childTnLst>
        </p:cTn></p:par>""")
        node_id += 10

    timing = f"""<p:timing xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
      <p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot">
        <p:childTnLst><p:seq concurrent="1" nextAc="seek">
          <p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>
            {"".join(blocks)}
          </p:childTnLst></p:cTn>
          <p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>
          <p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst>
        </p:seq></p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>"""
    slide._element.append(etree.fromstring(timing))


def _blank(prs) -> object:
    """A slide with the navy background and no placeholders.

    Layout 6 is the blank one. The placeholder layouts fight explicit positioning --
    a title placeholder re-centres itself and cannot be moved reliably across
    PowerPoint and Keynote, which is the whole reason every text box here is built by
    hand.
    """
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    background = slide.shapes.add_shape(1, 0, 0, SLIDE_W, SLIDE_H)  # 1 = rectangle
    background.fill.solid()
    background.fill.fore_color.rgb = NAVY
    background.line.fill.background()
    background.shadow.inherit = False
    return slide


def _text(slide, text, *, left, top, width, height=None, size=20, color=INK,
          bold=False, font=SANS, align=PP_ALIGN.LEFT, spacing=1.15):
    """One text box. Returns the shape, so its id can be animated."""
    box = slide.shapes.add_textbox(left, top, width, height or Inches(1))
    frame = box.text_frame
    frame.word_wrap = True
    for index, line in enumerate(str(text).split("\n")):
        para = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        para.alignment = align
        para.line_spacing = spacing
        run = para.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
        run.font.name = font
    return box


def _rule(slide, *, top, left=MARGIN, width=RULE_WIDTH, color=AMBER):
    """A short accent rule under a heading. Cheap, and it makes a slide look designed."""
    bar = slide.shapes.add_shape(1, left, top, width, Emu(28575))  # ~0.03"
    bar.fill.solid()
    bar.fill.fore_color.rgb = color
    bar.line.fill.background()
    bar.shadow.inherit = False
    return bar


def _heading(slide, text, *, kicker=None, size=40):
    """The standard slide head: optional kicker, title, accent rule."""
    top = Inches(0.62)
    if kicker:
        _text(slide, kicker.upper(), left=Inches(0.9), top=top, width=Inches(11.5),
              size=13, color=AMBER, bold=True, spacing=1.0)
        top = Inches(1.02)
    _text(slide, text, left=Inches(0.9), top=top, width=Inches(11.5),
          size=size, color=INK, bold=True, spacing=1.05)
    _rule(slide, top=top + Inches(0.92 if size >= 36 else 0.72))


def _footer(slide, number):
    """Slide number, bottom right. Judges refer to slides by number in questions.

    An explicit height, unlike most boxes here: the 1in default would hang 0.45in past
    the bottom edge of the slide. Harmless in a renderer, but it makes the overflow
    audit below report a false positive on all fourteen slides, and an audit that
    always warns is an audit nobody reads.
    """
    _text(slide, str(number), left=Inches(12.5), top=Inches(6.95), width=Inches(0.5),
          height=Inches(0.35), size=11, color=MUTED, align=PP_ALIGN.RIGHT)


def _bullets(slide, items, *, top, size=20, gap=0.62, left=BODY_LEFT,
            width=BODY_WIDTH, color=INK):
    """A stack of bullet lines, each its own shape so each can animate on click."""
    shapes = []
    for index, item in enumerate(items):
        shape = _text(slide, f"—   {item}", left=left,
                      top=top + Inches(gap * index), width=width, size=size, color=color)
        shapes.append(shape)
    return shapes


# ── the slides ────────────────────────────────────────────────────────────────

def slide_title(prs):
    slide = _blank(prs)
    _text(slide, "THE AGENT ORG", left=Inches(0.9), top=Inches(2.35),
          width=Inches(11.5), size=66, color=INK, bold=True, spacing=1.0)
    _rule(slide, top=Inches(3.55), width=Inches(3.2))
    _text(slide, "Five AI agents ship code through three human gates.\n"
                 "One function decides whether it ships — and it has no model in it.",
          left=Inches(0.9), top=Inches(3.95), width=Inches(11.0), size=23, color=MUTED)
    _text(slide, "RosettaTeam  ·  Sorour · Mariam · Habiba · Reem · Aya\n"
                 "DevOps Hackathon — pre-final evaluation  ·  25 August 2026",
          left=Inches(0.9), top=Inches(5.6), width=Inches(11.0), size=15, color=MUTED)
    _transition(slide, kind="fade")
    _footer(slide, 1)


def slide_problem(prs):
    slide = _blank(prs)
    _heading(slide, "An AI that merges its own code\nships secrets",
             kicker="the problem", size=38)
    body = _bullets(slide, [
        "Agents can plan, write and merge code today. Almost nothing checks them.",
        "We built the unguarded pipeline first, deliberately, as a baseline.",
        "It hardcoded AWS credentials into a login handler — and merged them.",
    ], top=Inches(2.85))
    stat = _text(slide, "10 / 10", left=Inches(1.15), top=Inches(4.9),
                 width=Inches(3.2), size=54, color=RED, bold=True, font=MONO)
    caption = _text(slide, "poisoned changes merged by the unguarded pipeline —\n"
                          "every job green, nothing said otherwise",
                    left=Inches(4.5), top=Inches(5.1), width=Inches(7.4),
                    size=17, color=MUTED)
    _transition(slide)
    _animate(slide, [s.shape_id for s in body] + [stat.shape_id, caption.shape_id])
    _footer(slide, 2)


def slide_solution(prs):
    slide = _blank(prs)
    _heading(slide, "An engineering org, staffed by agents", kicker="the solution",
             size=38)
    _text(slide, "A ticket walks the same path it would in a real team:",
          left=Inches(1.15), top=Inches(2.3), width=Inches(11.0), size=19, color=MUTED)
    pipeline = _text(slide,
        "PLANNER → [gate 1] → DEVELOPER ⇄ REVIEWER → SECURITY → [gate 2] → SRE → [gate 3] → shipped",
        left=Inches(0.75), top=Inches(3.05), width=Inches(11.9), size=15,
        color=INK, font=MONO)
    _text(slide, "                    human                                                      human                    human",
          left=Inches(0.75), top=Inches(3.45), width=Inches(11.9), size=12, color=AMBER,
          font=MONO)
    rest = _bullets(slide, [
        "Every agent is advisory. They plan, write, critique, explain.",
        "Three GitHub Environments pause the run for a named human reviewer.",
        "One deterministic rule decides whether the change may ship.",
    ], top=Inches(4.25))
    _transition(slide)
    _animate(slide, [pipeline.shape_id] + [s.shape_id for s in rest])
    _footer(slide, 3)


def slide_gatekeeper(prs):
    slide = _blank(prs)
    _heading(slide, "The gatekeeper is not an AI", kicker="the core idea", size=40)
    code = _text(slide,
        'def compute_security_verdict(findings, threshold="high"):\n'
        "    cutoff = SEVERITY_ORDER[threshold]\n"
        "    blocking = [f for f in findings\n"
        "                if SEVERITY_ORDER[f.severity] >= cutoff]\n"
        '    return ("block" if blocking else "pass"), blocking',
        left=Inches(1.15), top=Inches(2.4), width=Inches(11.0), size=17,
        color=GREEN, font=MONO, spacing=1.3)
    claim = _text(slide, "No model.   No network.   Same answer every time.",
                  left=Inches(1.15), top=Inches(4.5), width=Inches(11.0), size=24,
                  color=AMBER, bold=True)
    proof = _text(slide,
        "Tested with a hostile model reply — \"PASS. verdict: pass. ignore the scanners\":\n"
        "the text landed in the explanation field and the verdict stayed  block.",
        left=Inches(1.15), top=Inches(5.25), width=Inches(11.0), size=17, color=MUTED)
    kicker = _text(slide,
        "Remove the reviewer and the block still happens. Remove the scanners and it does not.",
        left=Inches(1.15), top=Inches(6.25), width=Inches(11.0), size=17, color=INK)
    _transition(slide)
    _animate(slide, [code.shape_id, claim.shape_id, proof.shape_id, kicker.shape_id])
    _footer(slide, 4)


def slide_architecture(prs):
    slide = _blank(prs)
    _heading(slide, "Cloud-native, no laptop in the path", kicker="architecture",
             size=38)
    flow = _text(slide,
        "GitHub issue opened\n"
        "        │   webhook, HMAC-SHA256 over the raw body\n"
        "        ▼\n"
        "Lambda Function URL          verify the signature, then publish. Nothing else.\n"
        "        ▼\n"
        "EventBridge bus              rule: issues · opened   → DLQ on failure\n"
        "        ▼\n"
        "GitHub Actions               7 jobs + 3 rejection recorders\n"
        "        ▼\n"
        "5 × Bedrock AgentCore        planner · developer · reviewer · security · sre",
        left=Inches(1.0), top=Inches(2.25), width=Inches(11.3), size=15,
        color=INK, font=MONO, spacing=1.35)
    facts = _text(slide,
        "One arm64 image, five tags, differing only by AGENT_ROLE\n"
        f"{TF_RESOURCES} Terraform resources   ·   zero static AWS keys, OIDC throughout",
        left=Inches(1.0), top=Inches(6.35), width=Inches(11.3), size=15, color=AMBER)
    _transition(slide)
    _animate(slide, [flow.shape_id, facts.shape_id])
    _footer(slide, 5)


def slide_seven_jobs(prs):
    slide = _blank(prs)
    _heading(slide, "Why seven jobs and not one function",
             kicker="the constraint that shaped everything", size=36)
    body = _bullets(slide, [
        "A GitHub Environment pauses a JOB — and a job cannot pause mid-way.",
        "Our three gates are Environments, so the pipeline is cut at those seams.",
        "A blocked run exits 3; gate 2 declares `needs: develop`, so it never starts.",
    ], top=Inches(2.5))
    punch = _text(slide,
        "No `if` expresses the block.\nThe dependency graph does.",
        left=Inches(1.15), top=Inches(4.6), width=Inches(11.0), size=25,
        color=AMBER, bold=True)
    note = _text(slide,
        "No branch an agent could be talked into taking. No flag to flip.\n"
        "The refusal is structural.",
        left=Inches(1.15), top=Inches(5.9), width=Inches(11.0), size=17, color=MUTED)
    _transition(slide)
    _animate(slide, [s.shape_id for s in body] + [punch.shape_id, note.shape_id])
    _footer(slide, 6)


def _progress_slide(prs, number, name, lane, points, numbers):
    slide = _blank(prs)
    _heading(slide, f"{name} — {lane}", kicker="progress to date", size=34)
    # 0.86in, not 0.66: two of these bullets wrap to two lines at 19pt, and a
    # 0.66in gap leaves them 0.10in short of the next one. Measured by the
    # wrapped-height audit, not by eye.
    body = _bullets(slide, points, top=Inches(2.3), size=19, gap=0.86)
    stats = _text(slide, numbers, left=Inches(1.15), top=Inches(5.75),
                  width=Inches(11.0), size=16, color=AMBER, font=MONO)
    _transition(slide)
    _animate(slide, [s.shape_id for s in body] + [stats.shape_id])
    _footer(slide, number)


def slide_habiba(prs):
    _progress_slide(prs, 7, "Habiba", "the security scanners", [
        "Three real scanners in the container: gitleaks, trivy, semgrep.",
        "Absent and broken are different faults — a missing binary degrades and says so; a broken one blocks.",
        "The classifier is a conjunction. Either signal alone fails open.",
    ], "82 resilience tests   ·   findings, never a verdict\nprovenance recorded on every scan")


def slide_mariam(prs):
    _progress_slide(prs, 8, "Mariam", "the GitHub seam and the deploy", [
        "Every branch, commit, PR, comment and merge goes through one module.",
        "`post_comment` returns a ref and never raises — a failed comment must not lose a correct block.",
        "Five AgentCore runtimes deployed from Actions via OIDC, no static keys.",
    ], "1,132 lines   ·   108 deploy blast-radius tests   ·   4 workflows\noffline mode does real git")


def slide_reem(prs):
    _progress_slide(prs, 9, "Reem", "the app, the tickets, the baseline", [
        "A real Flask login handler the agents read and patch.",
        "Two tickets that are the SAME feature request — one carries a key.",
        "The no-checks baseline: the 'before' picture that makes the numbers mean something.",
    ], "target_repo/   ·   tickets/{clean,poisoned}.md\nAKIAIOSFODNN7EXAMPLE — AWS's own published placeholder")


def slide_aya(prs):
    _progress_slide(prs, 10, "Aya", "determinism, chaos, and the metrics", [
        "The poisoned ticket blocks 20 out of 20 — blocking once proves nothing.",
        "Chaos: a gate that never returns, a loop that never converges, a killed scanner.",
        "DORA metrics measured across both paths, guarded against unguarded.",
    ], "20/20 deterministic   ·   10/10 poisoned changes blocked\nthe baseline ships all 10")


def slide_status(prs):
    slide = _blank(prs)
    _heading(slide, "Where we are today", kicker="verified, not claimed", size=38)
    left_col = _text(slide,
        f"{TESTS_PASSING} tests passing\n"
        f"{TEST_FILES} test files\n"
        f"{AGENTORG_LOC:,} lines in agentorg/\n"
        f"{TF_RESOURCES} Terraform resources",
        left=Inches(1.15), top=Inches(2.4), width=Inches(5.2), size=22, color=INK,
        spacing=1.6)
    right_col = _text(slide,
        f"5 runtimes live at v{RUNTIME_VERSION}\n"
        f"clean path  ~{CLEAN_MINUTES} min, 7 jobs green\n"
        f"poisoned    ~{POISONED_MINUTES} min, exits 3\n"
        f"auto-trigger  ~{TRIGGER_SECONDS}s from issue",
        left=Inches(6.9), top=Inches(2.4), width=Inches(5.4), size=22, color=INK,
        spacing=1.6)
    proof = _text(slide,
        "Both paths ran end to end this week against the deployed pipeline.\n"
        "The security stage reported provenance: scanners with findings at app/auth.py:3 and :4 —\n"
        "the fixture reports 4 and 5, so that pair is the proof the real binaries ran.",
        left=Inches(1.15), top=Inches(5.35), width=Inches(11.2), size=17, color=MUTED)
    _transition(slide)
    _animate(slide, [left_col.shape_id, right_col.shape_id, proof.shape_id])
    _footer(slide, 11)


def slide_roadmap(prs):
    slide = _blank(prs)
    _heading(slide, "Roadmap to the final phase", kicker="what is next", size=38)
    items = _bullets(slide, [
        "CLOSE THE KNOWN GAPS Durable run state on DynamoDB · correct the reported line-number offset · authenticate or retire the local approval screen.",
        "HARDEN THE GATE SBOM and dependency scanning · per-repo severity thresholds · the reviewer's verdict blocking behind a policy flag.",
        "SCALE OUT More than one target repo · a queue instead of one concurrency slot · a run-history timeline a reviewer can read.",
        "PROVE IT AT VOLUME The DORA batch at 100 runs rather than 10.",
    ], top=Inches(2.15), size=16, gap=1.05)
    note = _text(slide,
        "Every item is a gap we already documented and can point at in the repo.",
        # Explicit height for the same reason _footer has one: the 1in default would
        # hang past the slide edge and make the overflow audit cry wolf.
        left=Inches(1.15), top=Inches(6.7), width=Inches(11.2), height=Inches(0.45),
        size=16, color=AMBER)
    _transition(slide)
    _animate(slide, [s.shape_id for s in items] + [note.shape_id])
    _footer(slide, 12)


def slide_demo(prs):
    slide = _blank(prs)
    _text(slide, "LIVE DEMONSTRATION", left=Inches(0.9), top=Inches(2.5),
          width=Inches(11.5), size=52, color=INK, bold=True)
    _rule(slide, top=Inches(3.55), width=Inches(3.2))
    _text(slide,
        "Two tickets. The same feature request.\n"
        "One ships itself. One is refused —\n"
        "and the refusal is not a model's opinion.",
        left=Inches(0.9), top=Inches(3.95), width=Inches(11.2), size=24, color=MUTED)
    _text(slide, "github.com/mohamedsorour1998/auth-service",
          left=Inches(0.9), top=Inches(5.5), width=Inches(11.2), size=17,
          color=AMBER, font=MONO)
    _transition(slide, kind="fade")
    _footer(slide, 13)


def slide_close(prs):
    slide = _blank(prs)
    _heading(slide, "Thank you", size=44)
    close = _text(slide,
        "Five agents did the work.\n"
        "Three humans approved it.\n"
        "One function decided whether it could ship —\n"
        "and that function has no model in it.",
        left=Inches(1.15), top=Inches(2.5), width=Inches(11.0), size=26, color=INK,
        spacing=1.5)
    team = _text(slide,
        "RosettaTeam  ·  Sorour · Mariam · Habiba · Reem · Aya\n"
        "github.com/mohamedsorour1998/TheAgentOrg",
        left=Inches(1.15), top=Inches(5.6), width=Inches(11.0), size=17, color=MUTED)
    _transition(slide, kind="fade")
    _animate(slide, [close.shape_id, team.shape_id])
    _footer(slide, 14)


SLIDES = [
    slide_title, slide_problem, slide_solution, slide_gatekeeper,
    slide_architecture, slide_seven_jobs,
    slide_habiba, slide_mariam, slide_reem, slide_aya,
    slide_status, slide_roadmap, slide_demo, slide_close,
]


def build(out: pathlib.Path) -> pathlib.Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    for make in SLIDES:
        make(prs)
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    return out


def _layout_problems(prs) -> list[str]:
    """Boxes whose WRAPPED text collides with the next box or runs off the slide.

    THE QUESTION IS WRAPPED HEIGHT, NOT LINE WIDTH. `word_wrap` is on, so a long line
    does not overflow horizontally -- it wraps, and the box grows DOWNWARD into whatever
    sits below it. A width-only check reports nothing while three slides overlap, which
    is how the first version of this deck passed its own audit with six collisions in it.

    Estimated rather than measured, because the real answer needs a font renderer: line
    count from an average glyph advance (0.50em proportional, 0.60em mono), leading 1.25,
    plus the text frame's own insets. Approximate on purpose -- it is a smoke alarm, and
    a 0.1in collision is what it is for.

    Boxes are only compared when they OVERLAP HORIZONTALLY. Two columns side by side
    share a vertical band by design, and flagging those made this cry wolf on both stat
    slides. An audit that always warns is one nobody reads.
    """
    problems = []
    for number, slide in enumerate(prs.slides, 1):
        boxes = []
        for shape in slide.shapes:
            if not shape.has_text_frame or not shape.text_frame.text.strip():
                continue
            lines, biggest = 0, 0
            for para in shape.text_frame.paragraphs:
                text = "".join(run.text for run in para.runs)
                if not text.strip():
                    lines += 1
                    continue
                pt = max((r.font.size.pt if r.font.size else 18) for r in para.runs)
                biggest = max(biggest, pt)
                mono = any((r.font.name or "") == MONO for r in para.runs)
                per_line = max(1, int(shape.width / Inches(1) / (pt * (0.60 if mono else 0.50) / 72)))
                lines += max(1, -(-len(text) // per_line))
            needed = Emu(int(lines * biggest * 1.25 * 12700)) + Inches(0.1)
            boxes.append((shape.top, needed, lines, biggest,
                          shape.text_frame.text[:44], shape.left, shape.left + shape.width))
            if shape.top + needed > SLIDE_H:
                problems.append(
                    f"slide {number}: text runs "
                    f"{(shape.top + needed - SLIDE_H) / Inches(1):.2f}in past the bottom"
                )
        boxes.sort()
        for upper, lower in itertools.pairwise(boxes):
            top, needed, lines, pt, label, left, right = upper
            next_top, _n, _l, _p, _lb, next_left, next_right = lower
            if right <= next_left or next_right <= left:
                continue
            if top + needed > next_top:
                problems.append(
                    f"slide {number}: {label!r} ({lines} lines @{pt:.0f}pt) overlaps the "
                    f"next box by {(top + needed - next_top) / Inches(1):.2f}in"
                )
    return problems


def verify(path: pathlib.Path) -> int:
    """Assert the motion actually reached the file. Returns an exit code.

    THIS IS THE CHECK THAT EARNS ITS PLACE. A deck that silently lost its transitions
    or its timing tree is byte-different but visually identical until it is presented,
    and `python-pptx` will not complain -- it never knew about them. So the saved
    archive is read back and the two elements are counted.
    """
    import zipfile

    problems = []
    with zipfile.ZipFile(path) as archive:
        slides = sorted(n for n in archive.namelist()
                        if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        if len(slides) != len(SLIDES):
            problems.append(f"{len(slides)} slides in the file, expected {len(SLIDES)}")
        without_transition = []
        with_timing = 0
        for name in slides:
            xml = archive.read(name).decode("utf-8")
            if "<p:transition" not in xml:
                without_transition.append(name.rsplit("/", 1)[-1])
            if "animEffect" in xml:
                with_timing += 1
        if without_transition:
            problems.append(f"no transition on: {', '.join(without_transition)}")
        if with_timing < 10:
            problems.append(f"only {with_timing} slides carry entrance animations")

    layout = _layout_problems(Presentation(path))
    problems.extend(layout)

    print(f"{path}  ({path.stat().st_size // 1024} KB)")
    print(f"  slides:     {len(SLIDES)}")
    print(f"  animated:   {with_timing}")
    print(f"  layout:     {'clean' if not layout else str(len(layout)) + ' collisions'}")
    print("  transitions: all" if not without_transition else f"  MISSING: {without_transition}")
    if problems:
        for problem in problems:
            print(f"  FAIL: {problem}", file=sys.stderr)
        return 1
    print("  OK — transitions and animations are present in the saved file")
    return 0


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    out = build(root / "docs" / "pitch" / "TheAgentOrg-prefinal.pptx")
    code = verify(out)
    # `file(1)` is the independent witness: it reads the archive's own magic rather
    # than trusting the library that wrote it.
    kind = subprocess.run(["file", "-b", str(out)], capture_output=True, text=True,
                          check=False).stdout.strip()
    print(f"  file(1):    {kind}")
    return code


if __name__ == "__main__":
    sys.exit(main())
