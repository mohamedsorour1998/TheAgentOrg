/**
 * THE STAGE SPINE. The signature element of this product, and the one screen the
 * demo lives on.
 *
 * WHY A SPINE AND NOT A ROW OF PILLS
 * ==================================
 * The nine stages are a REAL sequence -- `plan gate1 develop review security
 * gate2 sre gate3 promote` -- so an ordered structural device encodes something
 * true rather than decorating.
 *
 * A GATE IS NOT A STAGE, AND THE MARK SAYS SO
 * ===========================================
 * Five of the nine are agents doing work; three are humans deciding; one merges.
 * A gate STOPS -- it holds the pipeline until a person clicks -- so it is drawn as
 * a hollow RING, larger, while an agent stage gets a filled dot. Rendering all
 * nine identically would make the three places a human is required look like six
 * places nobody is. The GitHub App's logo is the same mark for the same reason.
 *
 * WHEN A RUN BLOCKS THE SPINE TERMINATES, and that is deliberate. `develop` exits
 * 3 and `gate2` never starts -- no `if:` expresses that, the dependency graph
 * does. So the rail below a block is drawn as ended rather than pending: the
 * stages after it are not "waiting", they will never run.
 *
 * ── IT IS ALSO THE PAGE'S NAVIGATION, AND THAT IS THE REDESIGN ───────────────
 *
 * Reported from the deployed app: *"the run page is super unorganized and messy
 * ... make it smart and elegant and shorter"*. It was eight stacked sections, and
 * the four largest -- what the agents produced, the security verdict, the
 * decisions and the cost -- were all open at once, so the page was three screens
 * of scrolling whose most important beat sat in the middle of it.
 *
 * A run IS a sequence of stages, each of which produced something. So the spine
 * became the index: selecting a stage reveals what that stage produced, and
 * exactly one is open. That is a structural device encoding something true rather
 * than a tab strip bolted on -- the test this repository applies to any numbered
 * or sequential ornament, passed rather than assumed.
 *
 * **HORIZONTAL NOW, WHICH REVERSES THE EARLIER ARGUMENT.** The vertical version
 * said a horizontal stepper risks putting "the stage a person is waiting on" off
 * screen. True, and now answered elsewhere: the gate awaiting a decision has its
 * own card ABOVE this, so it is never the spine's job to keep it visible. The
 * spine wraps rather than scrolling, so no stage can be hidden either way.
 */

"use client";

import { GATES, STAGE_ORDER } from "@/lib/ci-view";
import type { Gate, Stage, StageView } from "@/lib/contract";

/** What each stage is, in the words a person would use. */
const WHAT: Readonly<Record<Stage, string>> = {
  plan: "Reads the ticket and writes the tasks",
  gate1: "A person approves the plan",
  develop: "Writes the change, then the scanners run",
  review: "A model reads the diff — advisory",
  security: "Three scanners and a fixed rule — binding",
  gate2: "A person approves the change",
  sre: "Measures CI and adds advice",
  gate3: "A person approves the release",
  promote: "Merges",
};

/**
 * How a stage is drawn. Derived from the job's status, not from its position:
 * position tells you what SHOULD have happened and the row tells you what did.
 */
export type Phase = "done" | "running" | "waiting" | "refused" | "pending" | "never";

function phaseOf(view: StageView | undefined, runEnded: boolean): Phase {
  if (!view) return runEnded ? "never" : "pending";
  switch (view.status) {
    case "done":
      return "done";
    case "claimed":
      return "running";
    case "paused":
      return "waiting";
    case "blocked":
    case "rejected":
    case "failed":
      return "refused";
    case "already_final":
      return "done";
    case "ready":
      return "pending";
  }
}

/**
 * The MARK's colour per phase. A border token is legitimate here -- a 2px ring at
 * low contrast reads as "inactive", which is the intent.
 *
 * DO NOT USE THESE AS TEXT COLOURS. See `PHASE_TEXT` below.
 */
const PHASE_COLOUR: Readonly<Record<Phase, string>> = {
  done: "var(--shipped)",
  running: "var(--accent)",
  waiting: "var(--accent)",
  refused: "var(--refused)",
  pending: "var(--border-strong)",
  never: "var(--border)",
};

/**
 * The WORD's colour per phase, and the two that differ are the whole reason this
 * second table exists.
 *
 * MEASURED, against `--surface`:
 *
 *     pending  --border-strong #2c3a4f   1.67:1   <- a border token as text
 *     never    --border        #1f2937   1.31:1   <- unreadable
 *     --text-muted             #8b97ab   6.50:1
 *
 * The first version of this component used `PHASE_COLOUR` for both, so on a
 * poisoned run every stage after the block rendered "did not run" at 1.31:1 --
 * the words that exist SO THE SPINE DOES NOT READ AS BLANK were the ones nobody
 * could read, and the demo's central beat lost its explanation on a projector.
 *
 * A border token and a text token are not interchangeable: 1.31:1 is correct for
 * a hairline and illegible for a sentence. Keeping two tables is what stops the
 * next edit collapsing them again.
 */
const PHASE_TEXT: Readonly<Record<Phase, string>> = {
  done: "var(--shipped)",
  running: "var(--accent)",
  waiting: "var(--accent)",
  refused: "var(--refused)",
  pending: "var(--text-muted)",
  never: "var(--text-muted)",
};

/** The word for a phase, said in full. Used in the sentence, not on the mark. */
export const PHASE_WORD: Readonly<Record<Phase, string>> = {
  done: "done",
  running: "running now",
  waiting: "waiting for a person",
  refused: "stopped here",
  pending: "not started",
  never: "did not run",
};

/** Every stage's phase, in order, with a block ending the run for those after it. */
export function phases(
  stages: StageView[],
  runEnded: boolean,
): { stage: Stage; phase: Phase; view: StageView | undefined }[] {
  const byStage = new Map<string, StageView>();
  // A reclaimed job can appear more than once, so the LAST row wins -- it is the
  // most recent transition.
  for (const s of stages) byStage.set(s.stage, s);

  const refusedAt = STAGE_ORDER.findIndex((name) => {
    const v = byStage.get(name);
    return (
      v !== undefined &&
      (v.status === "blocked" || v.status === "rejected" || v.status === "failed")
    );
  });

  return STAGE_ORDER.map((stage, i) => {
    const view = byStage.get(stage);
    // ONLY A STAGE WITH NO RECORD OF ITS OWN is drawn dead past a stop. The revision
    // cap stops the run at `review` and the scanners still run after the loop, so
    // `security` carries a real result there -- overriding it with "did not run"
    // erased exactly that, the same way run 71's block erased review and security.
    const stopped = refusedAt >= 0 && i > refusedAt && view === undefined;
    return { stage, view, phase: stopped ? "never" : phaseOf(view, runEnded) };
  });
}

/**
 * ONE SENTENCE FOR THE WHOLE RUN, under the spine.
 *
 * **THIS REPLACED NINE TINY LABELS AND IS BETTER, NOT MERELY SHORTER.** Nine phase
 * words across nine marks have to be set at `--step-caption` -- 11px, muted, below
 * this product's floor for a screen share, and precisely the size the vertical
 * spine refused to use for this exact word. Said once, at full size, it is legible
 * and it can say something the labels could not: that nothing after a block ran.
 */
export function spineSentence(
  rows: { stage: Stage; phase: Phase }[],
  awaiting: readonly Gate[],
  live: boolean,
): string {
  const open = rows.find((r) => awaiting.includes(r.stage as Gate));
  if (open) return `Waiting for your decision at ${open.stage}.`;

  const running = rows.find((r) => r.phase === "running");
  if (running) return `${running.stage} is running now. ${WHAT[running.stage]}.`;

  const stopped = rows.find((r) => r.phase === "refused");
  if (stopped) {
    // NAME THE LAST STAGE THAT RAN, which is not always the one that stopped: at the
    // revision cap the run stops at `review` and the scanners still run after it.
    const last = rows.filter((r) => r.phase !== "never" && r.phase !== "pending").at(-1);
    const after = last && last.stage !== stopped.stage ? last.stage : "it";
    return `Stopped at ${stopped.stage}. Nothing after ${after} ran — those stages are not waiting, they will never start.`;
  }

  const promoted = rows.find((r) => r.stage === "promote" && r.phase === "done");
  if (promoted) return "Every stage finished and the change was merged.";

  const done = rows.filter((r) => r.phase === "done").length;
  // `live: false` MEANS THE PAGE IS SHOWING THE STORED RECORD, which may be behind
  // -- see `RunDetail.live`. Saying "nothing is running" from a record that cannot
  // see a running job would be a claim this page has already made wrongly once.
  return live
    ? `${done} of ${STAGE_ORDER.length} stages done. Nothing is running.`
    : `${done} of ${STAGE_ORDER.length} stages recorded. This is the stored record, not a live reading.`;
}

export function StageSpine({
  stages,
  runEnded,
  awaitingGates,
  selected,
  onSelect,
}: {
  stages: StageView[];
  runEnded: boolean;
  awaitingGates: readonly Gate[];
  selected: Stage;
  onSelect: (stage: Stage) => void;
}) {
  const rows = phases(stages, runEnded);

  return (
    <ol className="spine">
      {rows.map(({ stage, phase }, i) => {
        const isGate = (GATES as readonly string[]).includes(stage);
        const open = isGate && awaitingGates.includes(stage as Gate);
        const colour = PHASE_COLOUR[phase];
        const stopped = phase === "never";

        return (
          <li key={stage} className="spine-step">
            <button
              type="button"
              className="spine-btn"
              aria-current={selected === stage}
              onClick={() => onSelect(stage)}
              // The accessible name carries what the mark says in colour, because
              // colour is not available to every reader -- and the phase word is
              // no longer rendered beside the mark for anyone.
              aria-label={`${stage}: ${open ? "waiting for your decision" : PHASE_WORD[phase]}. ${WHAT[stage]}`}
              title={WHAT[stage]}
            >
              <span className="spine-rail" aria-hidden="true">
                <span
                  className="spine-line"
                  style={{
                    background: i === 0 ? "transparent" : rail(rows[i - 1]?.phase ?? "pending", stopped),
                  }}
                />
                <span
                  style={{
                    flexShrink: 0,
                    width: isGate ? "0.95rem" : "0.6rem",
                    height: isGate ? "0.95rem" : "0.6rem",
                    borderRadius: "50%",
                    border: `2px solid ${colour}`,
                    // A GATE IS HOLLOW AND AN AGENT STAGE IS FILLED. A decision a
                    // person makes is a different kind of thing from a step that
                    // merely ran, and the ring is the product's own mark for it.
                    background:
                      !isGate && (phase === "done" || phase === "refused")
                        ? colour
                        : "transparent",
                    // THE ONLY TWO THINGS THAT MOVE: a stage genuinely executing,
                    // and a gate genuinely waiting on the person looking at it.
                    borderTopColor: phase === "running" ? "transparent" : undefined,
                    animation: open ? "pulse 1.8s ease-in-out infinite" : undefined,
                  }}
                  className={phase === "running" ? "spine-spinner" : undefined}
                />
                <span
                  className="spine-line"
                  style={{
                    background:
                      i === rows.length - 1 ? "transparent" : rail(phase, rows[i + 1]?.phase === "never"),
                  }}
                />
              </span>
              <span
                className="spine-name"
                style={{
                  // PHASE_TEXT, never PHASE_COLOUR -- the latter holds border
                  // tokens that measure 1.31:1 as text. And never a conditional
                  // beside it either: a special case here is a SECOND place the
                  // word's colour is decided, which is the drift the two tables
                  // exist to stop. `refusals.test.ts` caught exactly that.
                  color: PHASE_TEXT[phase],
                  fontWeight: selected === stage ? 600 : 400,
                }}
              >
                {stage}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The rail leaving a stage. Dashed past a block, because those stages will never
 * run -- which is different from not having run yet, and is the distinction the
 * whole spine exists to draw.
 */
function rail(phase: Phase, dead: boolean): string {
  if (dead || phase === "never") {
    return "repeating-linear-gradient(90deg, var(--border) 0 3px, transparent 3px 6px)";
  }
  // A stage not started yet gets the plain hairline rather than the mark's ring
  // token: the rail is a connector, and `--border-strong` between two dim marks
  // reads as a drawn relationship where there is not one yet.
  if (phase === "pending") return "var(--border)";
  return PHASE_COLOUR[phase];
}
