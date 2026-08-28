/**
 * THE STREAM'S DIFF. Two properties that pull in opposite directions.
 *
 * The heartbeat test is the one that matters most: the queue bumps `updated_at` on
 * every lease renewal with NO state change, so a diff that keyed on the timestamp
 * would emit a frame per renewal — a long `develop` stage flickering while nothing
 * progresses, on the screen the demo lives on.
 */

import { describe, expect, it } from "vitest";

import { type StageRow, framesFor, renderable } from "../stream";

const RUN = "11111111-2222-3333-4444-555555555555";

function row(overrides: Partial<StageRow> = {}): StageRow {
  return {
    stage: "develop",
    status: "claimed",
    attempt: 1,
    exit_code: null,
    updated_at: "2026-08-28T12:00:00.000000+00:00",
    ...overrides,
  };
}

describe("a fresh client", () => {
  it("gets a frame for every stage", () => {
    const rows = [
      row({ stage: "plan", status: "done", exit_code: 0 }),
      row({ stage: "gate1", status: "paused" }),
    ];
    const { events } = framesFor(rows, new Map(), "", RUN);
    expect(events.map((e) => e.stage)).toEqual(["plan", "gate1"]);
  });

  it("carries the exit code into the summary when there is one", () => {
    const { events } = framesFor(
      [row({ stage: "develop", status: "blocked", exit_code: 3 })],
      new Map(),
      "",
      RUN,
    );
    // EXIT 3 IS THE PIPELINE WORKING — the deterministic block rule — so it must
    // reach the screen rather than being flattened into "failed".
    expect(events[0]?.summary).toBe("blocked (exit 3)");
  });

  it("omits the exit code while a stage is still running", () => {
    const { events } = framesFor([row({ status: "claimed" })], new Map(), "", RUN);
    expect(events[0]?.summary).toBe("claimed");
  });
});

describe("a HEARTBEAT must not produce a frame", () => {
  it("sends nothing when only updated_at moved", () => {
    // ─────────────────────────────────────────────────────────────────────────
    // THE PROPERTY THIS FILE EXISTS FOR. `queue.heartbeat` renews a lease and bumps
    // `updated_at` with no state change. A diff keyed on the timestamp would emit a
    // frame for each renewal, and `develop` runs the developer/reviewer loop plus the
    // security scan under a 600-second lease — so a screen would flicker for minutes
    // while nothing happened.
    // ─────────────────────────────────────────────────────────────────────────
    const first = row({ updated_at: "2026-08-28T12:00:00.000000+00:00" });
    const { seen } = framesFor([first], new Map(), "", RUN);

    const renewed = row({ updated_at: "2026-08-28T12:00:30.000000+00:00" });
    const { events } = framesFor([renewed], seen, "", RUN);

    expect(events).toEqual([]);
  });

  it("still sends a frame when the STATUS changes at the same instant", () => {
    // Proves the check above is a diff on the rendered fields rather than a blanket
    // "same key, no frame" — otherwise a status change would also be suppressed and
    // the screen would never advance.
    const at = "2026-08-28T12:00:00.000000+00:00";
    const { seen } = framesFor([row({ status: "claimed", updated_at: at })], new Map(), "", RUN);
    const { events } = framesFor(
      [row({ status: "done", exit_code: 0, updated_at: at })],
      seen,
      "",
      RUN,
    );
    expect(events).toHaveLength(1);
    expect(events[0]?.status).toBe("done");
  });

  it("sends a frame for a SECOND ATTEMPT at the same stage", () => {
    // A retried stage is genuinely new to a reader, so `attempt` is part of the key
    // AND of the rendered form. Keyed on stage alone, a retry at the same status
    // would be silently swallowed.
    const { seen } = framesFor([row({ attempt: 1 })], new Map(), "", RUN);
    const { events } = framesFor([row({ attempt: 2 })], seen, "", RUN);
    expect(events).toHaveLength(1);
  });
});

describe("a RECONNECTING client", () => {
  it("is not re-sent a row at or before its cursor", () => {
    const cursor = "2026-08-28T12:00:00.000000+00:00";
    const rows = [
      row({ stage: "plan", status: "done", exit_code: 0, updated_at: cursor }),
      row({ stage: "gate1", status: "paused", updated_at: "2026-08-28T12:00:01.000000+00:00" }),
    ];
    const { events } = framesFor(rows, new Map(), cursor, RUN);
    // `>` not `>=`: a row AT the cursor is one the client already rendered. Sending it
    // again on a gate screen means a decision already actioned reappearing as new.
    expect(events.map((e) => e.stage)).toEqual(["gate1"]);
  });

  it("gets everything when its cursor is empty", () => {
    // The two conditions pull opposite ways, so this asserts the fresh-client case is
    // not broken by the resume logic.
    const rows = [row({ stage: "plan" }), row({ stage: "gate1" })];
    const { events } = framesFor(rows, new Map(), "", RUN);
    expect(events).toHaveLength(2);
  });

  it("compares cursors lexicographically, which ISO-8601 UTC supports", () => {
    // The queue already relies on this for lease comparison in both Python and SQL.
    // Asserted directly so a future change to a numeric cursor has to face it.
    const rows = [row({ updated_at: "2026-08-28T12:00:00.000000+00:00" })];
    expect(framesFor(rows, new Map(), "2026-08-28T11:59:59.999999+00:00", RUN).events)
      .toHaveLength(1);
    expect(framesFor(rows, new Map(), "2026-08-28T12:00:00.000001+00:00", RUN).events)
      .toHaveLength(0);
  });
});

describe("the diff key", () => {
  it("does NOT include updated_at", () => {
    // Asserted on the function rather than inferred from behaviour, so the reason the
    // heartbeat test passes is visible in one line.
    const a = renderable(row({ updated_at: "2026-08-28T12:00:00.000000+00:00" }));
    const b = renderable(row({ updated_at: "2026-08-28T23:59:59.000000+00:00" }));
    expect(a).toBe(b);
  });

  it("DOES include status, attempt and exit code", () => {
    const base = renderable(row());
    expect(renderable(row({ status: "done" }))).not.toBe(base);
    expect(renderable(row({ attempt: 2 }))).not.toBe(base);
    expect(renderable(row({ exit_code: 3 }))).not.toBe(base);
  });
});
