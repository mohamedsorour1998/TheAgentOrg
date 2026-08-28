/**
 * THE STAGE SPINE. The signature element of this product, and the one screen the
 * demo lives on.
 *
 * WHY A SPINE AND NOT A ROW OF PILLS
 * ==================================
 * The nine stages are a REAL sequence -- `plan gate1 develop review security
 * gate2 sre gate3 promote` -- so an ordered structural device encodes something
 * true rather than decorating. That is the test a numbered or sequential device
 * has to pass, and a horizontal stepper fails a different one: on a phone nine
 * steps either wrap into a meaningless grid or scroll out of sight, and the stage
 * a person is waiting on is the one thing that must never be off-screen.
 *
 * A GATE IS NOT A STAGE, AND THE MARK SAYS SO
 * ===========================================
 * Five of the nine are agents doing work; three are humans deciding; one merges.
 * A gate STOPS -- it holds the pipeline until a person clicks -- so it gets a
 * square, larger, drawn in the accent, while an agent stage gets a small dot.
 * Rendering all nine identically would make the three places a human is required
 * look like six places nobody is.
 *
 * WHEN A RUN BLOCKS THE SPINE TERMINATES, and that is deliberate. `develop` exits
 * 3 and `gate2` never starts -- no `if:` expresses that, the dependency graph
 * does. So the rail below a block is drawn as ended rather than pending: the
 * stages after it are not "waiting", they will never run.
 */

import type { Gate, Stage, StageView } from "@/lib/contract";

/** The nine stages in order. `contract.ts`'s `Stage` union, sequenced. */
export const STAGE_ORDER: readonly Stage[] = [
  "plan",
  "gate1",
  "develop",
  "review",
  "security",
  "gate2",
  "sre",
  "gate3",
  "promote",
];

/** The three that hold for a human. Nothing else is a gate. */
const GATES: ReadonlySet<string> = new Set<Gate>(["gate1", "gate2", "gate3"]);

/** What each stage is, in the words a person would use. */
const WHAT: Readonly<Record<Stage, string>> = {
  plan: "Reads the ticket and writes the tasks",
  gate1: "A person approves the plan",
  develop: "Writes the change, then the scanners run",
  review: "A model reads the diff — advisory",
  security: "Three scanners and five lines of Python — binding",
  gate2: "A person approves the change",
  sre: "Measures CI and adds advice",
  gate3: "A person approves the release",
  promote: "Merges",
};

/**
 * How a stage is drawn. Derived from the job's status, not from its position:
 * position tells you what SHOULD have happened and the row tells you what did.
 */
type Phase = "done" | "running" | "waiting" | "refused" | "pending" | "never";

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

const PHASE_COLOUR: Readonly<Record<Phase, string>> = {
  done: "var(--shipped)",
  running: "var(--accent)",
  waiting: "var(--accent)",
  refused: "var(--refused)",
  pending: "var(--border-strong)",
  never: "var(--border)",
};

/** The word beside a stage. `never` says why, rather than staying blank. */
const PHASE_WORD: Readonly<Record<Phase, string>> = {
  done: "done",
  running: "running now",
  waiting: "waiting for a person",
  refused: "stopped here",
  pending: "not started",
  never: "did not run",
};

export function StageSpine({
  stages,
  runEnded,
  awaitingGates,
}: {
  stages: StageView[];
  runEnded: boolean;
  awaitingGates: readonly Gate[];
}) {
  // Index by stage name. A reclaimed job can appear more than once, so the LAST
  // row wins -- it is the most recent transition.
  const byStage = new Map<string, StageView>();
  for (const s of stages) byStage.set(s.stage, s);

  // Where the run stopped, if it did. Everything after is `never`, not pending.
  const refusedAt = STAGE_ORDER.findIndex((name) => {
    const v = byStage.get(name);
    return (
      v !== undefined &&
      (v.status === "blocked" || v.status === "rejected" || v.status === "failed")
    );
  });

  return (
    <ol
      style={{
        listStyle: "none",
        margin: 0,
        padding: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {STAGE_ORDER.map((name, i) => {
        const view = byStage.get(name);
        const stopped = refusedAt >= 0 && i > refusedAt;
        const phase: Phase = stopped ? "never" : phaseOf(view, runEnded);
        const isGate = GATES.has(name);
        const open = isGate && awaitingGates.includes(name as Gate);
        const colour = PHASE_COLOUR[phase];
        const last = i === STAGE_ORDER.length - 1;

        return (
          <li
            key={name}
            style={{ display: "flex", gap: "var(--gap-4)", minHeight: "3.25rem" }}
          >
            {/* The rail: mark plus the line down to the next stage. */}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                flexShrink: 0,
                width: "1rem",
              }}
              aria-hidden="true"
            >
              <span
                style={{
                  width: isGate ? "0.85rem" : "0.5rem",
                  height: isGate ? "0.85rem" : "0.5rem",
                  marginTop: "0.4rem",
                  // A gate is a square, an agent stage a dot.
                  borderRadius: isGate ? "2px" : "50%",
                  border: `2px solid ${colour}`,
                  // Filled once it has happened; hollow while it has not.
                  background:
                    phase === "done" || phase === "refused" ? colour : "transparent",
                  // An open gate is the only thing on the page that moves.
                  animation: open ? "pulse 1.8s ease-in-out infinite" : undefined,
                }}
              />
              {last ? null : (
                <span
                  style={{
                    flex: 1,
                    width: "2px",
                    marginTop: "0.25rem",
                    background: stopped ? "var(--border)" : colour,
                    // Below a block the rail is dashed: those stages will never
                    // run, which is different from not having run yet.
                    backgroundImage: stopped
                      ? "repeating-linear-gradient(var(--border) 0 3px, transparent 3px 6px)"
                      : undefined,
                  }}
                />
              )}
            </div>

            <div style={{ paddingBottom: "var(--gap-4)", minWidth: 0 }}>
              <p
                style={{
                  margin: 0,
                  fontFamily: "var(--mono)",
                  fontSize: "var(--step-body)",
                  color: phase === "pending" || phase === "never" ? "var(--text-muted)" : "var(--text)",
                }}
              >
                {name}
                <span
                  style={{
                    marginLeft: "var(--gap-3)",
                    fontSize: "var(--step-caption)",
                    letterSpacing: "0.08em",
                    textTransform: "uppercase",
                    color: colour,
                  }}
                >
                  {open ? "your decision" : PHASE_WORD[phase]}
                </span>
              </p>
              <p
                style={{
                  margin: "var(--gap-1) 0 0",
                  fontSize: "var(--step-small)",
                  color: "var(--text-muted)",
                }}
              >
                {WHAT[name]}
              </p>
              {/* AT-LEAST-ONCE DELIVERY MADE VISIBLE. `reclaimed_from` is the
                  only trace that a stage may have run twice; hiding it would
                  leave a duplicate PR comment unexplained. */}
              {view?.reclaimed_from ? (
                <p
                  style={{
                    margin: "var(--gap-2) 0 0",
                    fontSize: "var(--step-small)",
                    color: "var(--refused)",
                  }}
                >
                  Reclaimed from a worker that stopped responding, so this stage
                  may have run twice.
                </p>
              ) : null}
              {view && view.attempt > 1 ? (
                <p
                  style={{
                    margin: "var(--gap-1) 0 0",
                    fontSize: "var(--step-caption)",
                    color: "var(--text-muted)",
                  }}
                >
                  Attempt {view.attempt}
                </p>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
