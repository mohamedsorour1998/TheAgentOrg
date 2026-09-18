/**
 * START A RUN — the control the product did not have.
 *
 * Until now a run could be started by opening an issue on the target repository or
 * by typing `gh workflow run`. Neither is available to somebody looking at this
 * screen, so the application had a run LIST and no way to produce a run.
 *
 * **THIS FORM ASKED FOR AN ISSUE NUMBER, AND THAT WAS THE WRONG FLOW.** The operator
 * said so plainly: it meant leaving the product, opening GitHub, filing an issue and
 * copying a number back. The number was never the point — it exists because
 * `github_ops.post_comment` needs somewhere to put the stage comments, and CLAUDE.md
 * records what happens without one:
 *
 *     [post_comment] ticket 'CLEAN-VERIFY' is not an issue number, so there is no
 *     issue to comment on
 *
 * every stage comment going *nowhere, silently, while every job stays green*. So the
 * server creates the issue instead, and the number never reaches a person.
 *
 * **THE FIRST FIELD IS THE TICKET THE AGENTS READ**, not a label for one.
 * `modules/ingress` sends the issue TITLE and never the body, because a body is
 * unbounded and goes straight into an agent prompt — so the optional second field is
 * for whoever reads the issue later, and says so.
 *
 * **THE POISONED CHECKBOX IS THE DEMO'S SECOND BEAT.** It makes the developer agent
 * put a fake AWS key in the change, so the scanners find it and the run is refused.
 * That is the whole thesis of this project shown in one run.
 *
 * **THE FIRST WORDING WAS REJECTED FOR BEING CONFUSING, AND THE OPERATOR WAS RIGHT.**
 * It read "Seed a committed credential, so the scanners block it. This asks the
 * pipeline to write AWS's published example key into the pull request on purpose."
 * Accurate, and it explained the MECHANISM to somebody asking what the box does.
 * "Demonstrate a blocked run" answers the question first; the mechanism follows in
 * one sentence. The key is AWS's own published example
 * (`AKIAIOSFODNN7EXAMPLE`) and authenticates nothing, which is why this is safe to
 * offer as a button at all — but that detail belongs in the docs, not on the
 * control.
 *
 * **NO RUN ID COMES BACK.** `workflow_dispatch` answers 204 with no body; the id is
 * minted by the `plan` job and appears here only once `run_index.record_run` writes
 * it. So this refreshes the list rather than navigating to a run that does not exist
 * yet — and says the run takes a moment to appear, because a list that does not
 * change instantly otherwise reads as a button that did nothing.
 */

"use client";

import { useCallback, useState } from "react";

type Answer = { error?: string; detail?: string; issue?: string };

export function StartRun({ onStarted }: { onStarted?: () => void }) {
  const [title, setTitle] = useState("");
  const [detail, setDetail] = useState("");
  const [poisoned, setPoisoned] = useState(false);
  /**
   * WHICH REPOSITORY, AND THE PRODUCT COULD NOT ASK BEFORE.
   *
   * The target was `DEMO_REPO`, a repository variable on the workflow — so a
   * tenant could put three repositories in scope, see all three on the
   * repositories screen, and every run still went to whichever one the variable
   * named. Reported from the deployed app: *"when creating an issue we should be
   * able to select which repo"*.
   *
   * THE OPTIONS ARE THE TENANT'S SCOPE, not everything the person can reach on
   * GitHub. Scope is what `web/lib/authz.ts` checks before it will let anybody
   * approve a gate, so offering a repository outside it would create a run this
   * tenant could never then approve. The server refuses one anyway — this list
   * exists so nobody is offered the refusal in the first place.
   */
  const [repositories, setRepositories] = useState<string[]>([]);
  const [repository, setRepository] = useState("");
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
          body: JSON.stringify({ title, body: detail, poisoned, repository }),
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
      // THE ISSUE NUMBER IS REAL AND THE RUN ID IS NOT, so this names the one the
      // person can actually open. `workflow_dispatch` answers 204 with no body; the
      // run id is minted by the `plan` job and reaches this application only when
      // `run_index.record_run` writes it.
      // NAMES THE REPOSITORY. With a choice available, "the target repository" is
      // the one thing the confirmation must not be vague about -- it is the only
      // way to notice that the wrong one was selected, while the run is young
      // enough to cancel.
      setNotice(
        `Issue #${answer.issue} opened on ${repository || "the target repository"}. ` +
          `The run appears in the list once the plan stage starts, which takes ` +
          `about a minute.`,
      );
      setTitle("");
      setDetail("");
      onStarted?.();
    },
    [title, detail, poisoned, repository, onStarted],
  );

  /**
   * READ WHEN THE FORM IS OPENED, not on every render of the page.
   *
   * `<details>` keeps this closed until somebody means to start a run, so loading
   * the scope on mount would be a request per visit to a list screen. The `open`
   * event fires once per opening and the guard makes it once per mount.
   */
  const loadScope = useCallback(() => {
    if (repositories.length > 0) return;
    void (async () => {
      try {
        const response = await fetch("/api/repositories");
        const answer = (await response.json()) as { repositories?: { full_name?: string }[] };
        const names = (answer.repositories ?? [])
          .map((r) => String(r.full_name ?? ""))
          .filter(Boolean);
        setRepositories(names);
        // PRESELECTED, because a select with no value submits nothing and the
        // server would then choose for them -- which is the behaviour this control
        // exists to replace.
        setRepository((current) => current || names[0] || "");
      } catch {
        // SILENT, and the select simply does not appear. The run still starts:
        // an omitted repository falls back to the tenant's first, which is what
        // every run did before this control existed. Blocking the form on a
        // failed list would take away a capability rather than adding one.
      }
    })();
  }, [repositories.length]);

  return (
    <details className="card" style={{ maxWidth: "var(--measure)" }} onToggle={loadScope}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>Start a run</summary>

      <form onSubmit={submit} style={{ display: "grid", gap: "var(--gap-3)", marginTop: "var(--gap-4)" }}>
        {problem ? (
          <p role="alert" className="prose" style={{ margin: 0, fontSize: "var(--step-small)", color: "var(--refused)" }}>
            {problem.error}
            {problem.detail ? <span style={{ opacity: 0.85 }}> — {problem.detail}</span> : null}
          </p>
        ) : null}
        {notice && !problem ? (
          <p role="status" className="prose" style={{ margin: 0, fontSize: "var(--step-small)" }}>
            {notice}
          </p>
        ) : null}

        {/* ONLY WHEN THERE IS A CHOICE TO MAKE. One repository in scope means the
            select has one option and answers a question nobody asked; the run goes
            there either way. Two or more, and it is the first thing to decide. */}
        {repositories.length > 1 ? (
          <label style={{ display: "grid", gap: "var(--gap-1)" }}>
            <span className="eyebrow">Which repository?</span>
            <select
              className="field"
              value={repository}
              onChange={(e) => setRepository(e.target.value)}
            >
              {repositories.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        <label style={{ display: "grid", gap: "var(--gap-1)" }}>
          <span className="eyebrow">What should change?</span>
          <input
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Add a per-IP rate limit of five login attempts per minute to app/auth.py, returning HTTP 429"
            style={{ padding: "var(--gap-2)", font: "inherit" }}
          />
        </label>
        {/* STATED, because a vague ticket does not fail loudly -- it ends at the
            revision cap with the reviewer correctly withholding approval, which
            looks like a broken pipeline. CLAUDE.md measured that twice.

            This line IS the ticket the agents read: `modules/ingress` sends the
            issue TITLE and never the body, because a body is unbounded and goes
            straight into an agent prompt. */}
        <p className="prose" style={{ margin: 0, fontSize: "var(--step-small)", opacity: 0.8 }}>
          One sentence, specific. This becomes the issue title and is what the agents
          read. A vague ticket ends with the reviewer refusing to approve a change
          that does not implement it — correct, and it looks like a failure.
        </p>

        <label style={{ display: "grid", gap: "var(--gap-1)" }}>
          <span className="eyebrow">Anything else (optional)</span>
          <textarea
            rows={2}
            value={detail}
            onChange={(e) => setDetail(e.target.value)}
            placeholder="Context for whoever reads the issue later. The agents do not read this."
            style={{ padding: "var(--gap-2)", font: "inherit", resize: "vertical" }}
          />
        </label>

        <label style={{ display: "flex", gap: "var(--gap-3)", alignItems: "flex-start" }}>
          <input
            type="checkbox"
            checked={poisoned}
            onChange={(e) => setPoisoned(e.target.checked)}
            style={{ width: "1.1rem", height: "1.1rem", marginTop: "0.2rem", accentColor: "var(--accent)" }}
          />
          <span style={{ fontSize: "var(--step-small)" }}>
            <strong style={{ fontWeight: 500 }}>Demonstrate a blocked run.</strong>{" "}
            The agent deliberately leaves a fake AWS key in the code. The scanners
            find it and the run is refused, so nothing is merged — this is how you
            watch the security gate work.
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
