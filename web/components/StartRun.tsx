/**
 * START A RUN — the control the product did not have.
 *
 * Until now a run could be started by opening an issue on the target repository or
 * by typing `gh workflow run`. Neither is available to somebody looking at this
 * screen, so the application had a run LIST and no way to produce a run.
 *
 * **THE TICKET IS AN ISSUE NUMBER, AND THE FIELD SAYS SO.** Measured and recorded
 * in CLAUDE.md: a ticket that is not a bare number still runs, and every stage
 * comment goes *nowhere, silently, while every job stays green* —
 *
 *     [post_comment] ticket 'CLEAN-VERIFY' is not an issue number, so there is no
 *     issue to comment on
 *
 * So the label names what it is, the route refuses anything else, and the help text
 * says where the comments land. A field labelled "ticket" would have been guessed
 * wrong by everybody.
 *
 * **THE POISONED CHECKBOX IS THE DEMO'S SECOND BEAT, AND IT IS LABELLED AS A
 * DELIBERATE ACT.** It makes the developer agent seed a real AWS example credential
 * into the diff so the scanners block it. That is the whole thesis of this project
 * shown in one run — and it is also, literally, asking the pipeline to write a
 * credential into a pull request. The copy says that plainly rather than calling it
 * "demo mode".
 *
 * **NO RUN ID COMES BACK.** `workflow_dispatch` answers 204 with no body; the id is
 * minted by the `plan` job and appears here only once `run_index.record_run` writes
 * it. So this refreshes the list rather than navigating to a run that does not exist
 * yet — and says the run takes a moment to appear, because a list that does not
 * change instantly otherwise reads as a button that did nothing.
 */

"use client";

import { useCallback, useState } from "react";

type Answer = { error?: string; detail?: string };

export function StartRun({ onStarted }: { onStarted?: () => void }) {
  const [ticketId, setTicketId] = useState("");
  const [ticketText, setTicketText] = useState("");
  const [poisoned, setPoisoned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Answer | null>(null);
  const [notice, setNotice] = useState("");

  const submit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setBusy(true);
      setProblem(null);
      setNotice("");
      let answer: Answer = {};
      try {
        const response = await fetch("/api/runs", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            ticket_id: ticketId.trim(),
            ticket_text: ticketText,
            poisoned,
          }),
        });
        answer = (await response.json().catch(() => ({}))) as Answer;
        if (!response.ok) {
          setProblem({ error: answer.error ?? "the run could not be started", detail: answer.detail });
          return;
        }
      } catch {
        setProblem({ error: "the network request did not complete" });
        return;
      } finally {
        setBusy(false);
      }

      // THE WAIT IS NAMED. The `plan` job has to start and write an index row
      // before the run appears, which takes tens of seconds -- and a list that
      // does not change reads as a button that did nothing.
      setNotice(
        "Dispatched. The run appears in the list once the plan stage starts, which " +
          "takes about a minute.",
      );
      setTicketText("");
      onStarted?.();
    },
    [ticketId, ticketText, poisoned, onStarted],
  );

  return (
    <details className="card" style={{ maxWidth: "var(--measure)" }}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>Start a run</summary>

      <form onSubmit={submit} style={{ display: "grid", gap: "var(--gap-3)", marginTop: "var(--gap-4)" }}>
        {problem ? (
          <p role="alert" className="prose" style={{ margin: 0, fontSize: "var(--step-small)", color: "var(--rose)" }}>
            {problem.error}
            {problem.detail ? <span style={{ opacity: 0.85 }}> — {problem.detail}</span> : null}
          </p>
        ) : null}
        {notice && !problem ? (
          <p role="status" className="prose" style={{ margin: 0, fontSize: "var(--step-small)" }}>
            {notice}
          </p>
        ) : null}

        <label style={{ display: "grid", gap: "var(--gap-1)" }}>
          <span className="eyebrow">Issue number on the target repository</span>
          <input
            inputMode="numeric"
            pattern="[0-9]+"
            required
            value={ticketId}
            onChange={(e) => setTicketId(e.target.value)}
            placeholder="59"
            style={{ padding: "var(--gap-2)", font: "inherit", maxWidth: "12ch" }}
          />
        </label>

        <label style={{ display: "grid", gap: "var(--gap-1)" }}>
          <span className="eyebrow">What the change should do</span>
          <textarea
            required
            rows={3}
            value={ticketText}
            onChange={(e) => setTicketText(e.target.value)}
            placeholder="Add a per-IP rate limit of five login attempts per minute to app/auth.py, returning HTTP 429 past the threshold."
            style={{ padding: "var(--gap-2)", font: "inherit", resize: "vertical" }}
          />
        </label>
        {/* STATED, because a vague ticket does not fail loudly -- it ends at the
            revision cap with the reviewer correctly withholding approval, which
            looks like a broken pipeline. CLAUDE.md measured that twice. */}
        <p className="prose" style={{ margin: 0, fontSize: "var(--step-small)", opacity: 0.8 }}>
          Be specific. A vague ticket ends with the reviewer refusing to approve a
          change that does not implement it — which is correct, and looks like a
          failure.
        </p>

        <label style={{ display: "flex", gap: "var(--gap-3)", alignItems: "flex-start" }}>
          <input
            type="checkbox"
            checked={poisoned}
            onChange={(e) => setPoisoned(e.target.checked)}
            style={{ width: "1.1rem", height: "1.1rem", marginTop: "0.2rem", accentColor: "var(--accent)" }}
          />
          <span style={{ fontSize: "var(--step-small)" }}>
            Seed a committed credential, so the scanners block it. This asks the
            pipeline to write AWS&apos;s published example key into the pull request
            on purpose — the change is refused and never merged.
          </span>
        </label>

        <div>
          <button type="submit" className="btn" disabled={busy}>
            {busy ? "Dispatching…" : "Start run"}
          </button>
        </div>
      </form>
    </details>
  );
}
