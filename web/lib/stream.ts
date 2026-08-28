/**
 * WHAT THE STREAM SENDS, as pure functions. The testable half of I4.
 *
 * Extracted from `web/app/api/runs/[runId]/events/route.ts` because the interesting
 * logic there is a DIFF, and a diff inside a `ReadableStream` inside a route handler
 * is reachable only by opening a socket. Two of its properties would otherwise be
 * confidence that cannot be falsified:
 *
 *   * a heartbeat must NOT produce a frame — the queue bumps `updated_at` on every
 *     lease renewal with no state change, so a naive diff emits a frame every renewal
 *     and a long `develop` stage flickers while nothing progresses;
 *   * a reconnecting client must NOT be re-sent what it already rendered, and a fresh
 *     client MUST get the whole current picture. Those pull in opposite directions and
 *     only a test distinguishes getting both right from getting one right.
 */

import type { RunEvent } from "./endpoints";

/** One row of the queue's view of a stage, as the reader answers it. */
export interface StageRow {
  stage: string;
  status: string;
  attempt: number;
  exit_code: number | null;
  updated_at: string;
}

/**
 * The fields a screen renders, as one string. THE DIFF KEY.
 *
 * `updated_at` is deliberately ABSENT. Including it would make every heartbeat a
 * change, which is the flicker described above. `attempt` IS included because a
 * second attempt at the same stage is genuinely new to a reader.
 */
export function renderable(row: StageRow): string {
  return `${row.stage} ${row.status} ${row.attempt} ${row.exit_code}`;
}

/** The identity of a stage row for diffing. Stage plus attempt, not stage alone. */
export function rowKey(row: StageRow): string {
  return `${row.stage} ${row.attempt}`;
}

/**
 * Which rows deserve a frame, given what has already been sent and where the client
 * resumed from.
 *
 * `seen` maps `rowKey` to the last `renderable` sent for it. `since` is the client's
 * cursor — empty means a fresh client, which gets everything.
 *
 * TWO CONDITIONS, BOTH REQUIRED. A row is sent when its rendered form CHANGED **and**
 * it is after the cursor. Dropping the first re-sends unchanged rows on every tick;
 * dropping the second re-sends the whole history to a reconnecting client, which on a
 * gate screen means a decision that was already actioned reappearing as new.
 */
export function framesFor(
  rows: readonly StageRow[],
  seen: ReadonlyMap<string, string>,
  since: string,
  runId: string,
): { events: RunEvent[]; seen: Map<string, string> } {
  const events: RunEvent[] = [];
  const next = new Map<string, string>();

  for (const row of rows) {
    const key = rowKey(row);
    const rendered = renderable(row);
    next.set(key, rendered);

    const changed = seen.get(key) !== rendered;
    // `>` not `>=`: a row AT the cursor is one the client already has. ISO-8601 UTC
    // sorts lexicographically, which is the property the queue already relies on for
    // lease comparison in both Python and SQL.
    const afterCursor = since === "" || row.updated_at > since;

    if (changed && afterCursor) {
      events.push({
        cursor: row.updated_at,
        run_id: runId,
        kind: "stage",
        stage: row.stage,
        status: row.status,
        summary:
          row.exit_code === null
            ? row.status
            : `${row.status} (exit ${row.exit_code})`,
      });
    }
  }

  return { events, seen: next };
}
