/**
 * THE LIVE STREAM. `GET /api/runs/[runId]/events`, resumed from `?since=<cursor>`.
 *
 * WHAT THIS SHOWS WHILE NOTHING IS HAPPENING -- the question worth answering
 * before any code, because most of a run's wall-clock time is exactly that. A
 * stage takes tens of seconds and a gate waits for a human indefinitely, so a
 * "live" view is mostly a view of a pause.
 *
 * Three states, and none of them is a spinner:
 *
 *   connecting  the stream is opening. Said once, briefly.
 *   live        connected. The last frame's time is shown, so a person can see
 *               the stream is current even when no stage has moved -- silence
 *               with a fresh heartbeat means "waiting", silence with a stale one
 *               means "something is wrong", and a spinner cannot tell them apart.
 *   dropped     the connection ended and did not come back. Named, with the
 *               cursor it reached, and a manual retry.
 *
 * A `heartbeat` frame carries no stage transition and is DELIBERATELY not shown in
 * the event list -- it would fill the log with rows that say nothing. It updates
 * the "last heard from" time instead, which is the thing it is evidence of.
 *
 * WHY THE CURSOR IS KEPT IN A REF, NOT ONLY IN STATE
 * =================================================
 * `EventSource`'s handler closes over the render in which it was created. Reading
 * the cursor from state inside `onerror` would read the value from the render that
 * OPENED the stream -- so a reconnect after fifty frames would resume from frame
 * zero and replay everything. The ref is what makes the resume honest.
 *
 * NO AUTOMATIC RETRY LOOP. `EventSource` reconnects on its own, and a second loop
 * on top of it produces two streams racing to append to one list. When it gives up
 * this component says so and offers a button rather than hammering the endpoint.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { RunEvent } from "@/lib/endpoints";

export type StreamPhase = "connecting" | "live" | "dropped";

export interface Stream {
  phase: StreamPhase;
  /** Newest last. Heartbeats excluded -- they are evidence, not events. */
  events: RunEvent[];
  /** When any frame last arrived, heartbeats included. `null` before the first. */
  lastHeard: Date | null;
  /** How far the stream got. Shown when it drops, so a resume is verifiable. */
  cursor: string | null;
  /** Manual reopen, for the dropped state. */
  reconnect: () => void;
}

export function useRunStream(runId: string, enabled: boolean): Stream {
  const [phase, setPhase] = useState<StreamPhase>("connecting");
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [lastHeard, setLastHeard] = useState<Date | null>(null);
  const [attempt, setAttempt] = useState(0);

  // The cursor a reconnect must resume from. A ref, not state -- see the header.
  const cursorRef = useRef<string | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);

  const reconnect = useCallback(() => {
    setPhase("connecting");
    setAttempt((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!enabled) return;

    const since = cursorRef.current;
    const url = since
      ? `/api/runs/${encodeURIComponent(runId)}/events?since=${encodeURIComponent(since)}`
      : `/api/runs/${encodeURIComponent(runId)}/events`;

    const source = new EventSource(url, { withCredentials: true });

    source.onopen = () => setPhase("live");

    source.onmessage = (message: MessageEvent<string>) => {
      setPhase("live");
      setLastHeard(new Date());

      let frame: RunEvent;
      try {
        frame = JSON.parse(message.data) as RunEvent;
      } catch {
        // A frame we cannot parse is dropped rather than rendered. It is not
        // worth breaking the view over, and the "last heard" time already
        // records that the stream is alive.
        return;
      }

      if (frame.cursor) {
        cursorRef.current = frame.cursor;
        setCursor(frame.cursor);
      }
      // Heartbeats update liveness and nothing else.
      if (frame.kind === "heartbeat") return;
      setEvents((prior) => [...prior, frame]);
    };

    source.onerror = () => {
      // `EventSource` retries internally while readyState is CONNECTING. Only a
      // CLOSED socket is genuinely dropped, and conflating them would report a
      // brief reconnect as a failure.
      if (source.readyState === EventSource.CLOSED) setPhase("dropped");
    };

    return () => source.close();
  }, [runId, enabled, attempt]);

  return { phase, events, lastHeard, cursor, reconnect };
}

/** How long ago, in words. Seconds matter here; a run moves in tens of them. */
export function ago(from: Date, now: Date): string {
  const seconds = Math.max(0, Math.round((now.getTime() - from.getTime()) / 1000));
  if (seconds < 2) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ago`;
}
