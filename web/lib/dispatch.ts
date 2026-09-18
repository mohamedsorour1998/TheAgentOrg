/**
 * Start a pipeline run from the application.
 *
 * **WHY THIS EXISTS.** Until now a run could be started two ways: open an issue on
 * the target repository (EventBridge dispatches it), or type `gh workflow run`.
 * Neither is available to somebody looking at the application, so the product had a
 * run LIST and no way to produce a run — which reads as a demo that only works if
 * you already know the CLI.
 *
 * **IT IS THE SAME CALL EVENTBRIDGE ALREADY MAKES.** `modules/ingress` POSTs to
 * `run-pipeline.yml/dispatches` with the same five inputs and the same token. This
 * adds no new path into the pipeline and no new credential — it makes an existing
 * one reachable from a browser, behind a session.
 *
 * **`trigger` IS `"ui"`, AND THE VALUE HAS TO BE NEW.** `RunState.trigger` exists
 * because no Actions field can answer "how did this run start?" — EventBridge
 * dispatches through the same REST API `gh workflow run` uses, so both report
 * `event: workflow_dispatch`. `tests/test_trigger_provenance.py` asserts the values
 * DIFFER, because identical ones would make a run recording the value
 * indistinguishable from a run whose trigger was never set. `issue` means the rule
 * sent it, `manual` means somebody typed it, and now `ui` means somebody clicked it.
 *
 * **THE TICKET ID MUST BE A BARE ISSUE NUMBER, AND THAT IS NOT A FORMALITY.**
 * Measured and recorded in CLAUDE.md: dispatching `ticket_id=CLEAN-VERIFY` logs
 *
 *     [post_comment] ticket 'CLEAN-VERIFY' is not an issue number, so there is no
 *     issue to comment on
 *
 * and every stage comment goes **nowhere, silently, while every job stays green**.
 * So this refuses a non-numeric id rather than accepting one and producing a run
 * whose entire visible output is missing.
 */

import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";

const REGION = process.env.AWS_REGION ?? "us-east-1";

/** The workflow, by file name. A literal: a caller must not choose what runs. */
const WORKFLOW = "run-pipeline.yml";

/** This repository, which is where the pipeline lives — not the target repo. */
const PIPELINE_REPO = process.env.PIPELINE_REPO ?? "mohamedsorour1998/TheAgentOrg";

/** Written by Terraform's `modules/ingress`; the same secret EventBridge reads. */
const TOKEN_SECRET =
  process.env.DISPATCH_TOKEN_SECRET_NAME ?? "theagentorg-shared-github-dispatch-token";

export class DispatchRefused extends Error {
  constructor(
    message: string,
    readonly detail = "",
  ) {
    super(message);
    this.name = "DispatchRefused";
  }
}

let cachedToken: string | null = null;

async function dispatchToken(): Promise<string> {
  if (cachedToken) return cachedToken;
  const sm = new SecretsManagerClient({ region: REGION });
  let raw: string | undefined;
  try {
    raw = (await sm.send(new GetSecretValueCommand({ SecretId: TOKEN_SECRET }))).SecretString;
  } catch (error) {
    // NAMED, because the two causes want different fixes and both present as
    // "starting a run is broken": the secret has no value (a human writes it
    // once, by design — Terraform creates the container only), or the compute
    // role cannot read it (apply
    // `aws_iam_role_policy.amplify_compute_may_read_dispatch_token`).
    throw new DispatchRefused(
      "starting runs is not configured",
      `${TOKEN_SECRET}: ${(error as Error).name}`,
    );
  }
  const token = (raw ?? "").trim();
  if (!token) {
    throw new DispatchRefused("starting runs is not configured", `${TOKEN_SECRET} is empty`);
  }
  cachedToken = token;
  return token;
}

/** The repository the pipeline opens pull requests against. */
const TARGET_REPO = process.env.DEMO_REPO ?? "mohamedsorour1998/auth-service";

export type RunRequest = {
  /** One line: what the change should do. Becomes the issue TITLE. */
  title: string;
  /** Optional detail. Becomes the issue BODY, and is not read by any agent. */
  body?: string;
  /** Seed a fake credential, to demonstrate the block. */
  poisoned: boolean;
  /**
   * `owner/name` to act on. Empty falls back to the deployment's `DEMO_REPO`.
   *
   * **THIS IS NOT WHERE IT IS AUTHORISED.** `app/api/runs/route.ts` refuses a
   * repository outside the caller's tenant-scoped list before this is reached, and
   * before the dispatch token is read. Checking it here would put the check on the
   * far side of the credential it exists to gate.
   */
  repository?: string;
};

/**
 * Open an issue on the target repository, and answer its number.
 *
 * **ASKING A PERSON FOR AN ISSUE NUMBER WAS THE WRONG FLOW, AND THE OPERATOR SAID
 * SO.** The first version of this form had a field labelled "Issue number on the
 * target repository", which meant leaving the product, opening GitHub, filing an
 * issue and copying a number back. The number was never the point — it exists
 * because `github_ops.post_comment` needs somewhere to put the stage comments.
 *
 * **THE ISSUE BODY IS DELIBERATELY NOT THE TICKET TEXT THE AGENTS READ.**
 * `modules/ingress` records why the transformer sends the issue TITLE and never the
 * body: the body is unbounded, may contain anything, and goes straight into an agent
 * prompt. The title is the ticket; the body is for a human reading the issue later.
 */
async function createIssue(title: string, body: string, repo: string): Promise<string> {
  const token = await dispatchToken();
  const response = await fetch(`https://api.github.com/repos/${repo}/issues`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      accept: "application/vnd.github+json",
      "content-type": "application/json",
      "user-agent": "theagentorg-web",
    },
    body: JSON.stringify({ title, body }),
  });
  if (response.status !== 201) {
    const text = await response.text().catch(() => "");
    throw new DispatchRefused(
      "the issue could not be created",
      `GitHub answered ${response.status} for ${repo}: ${text.slice(0, 160)}`,
    );
  }
  const issue = (await response.json()) as { number?: number };
  if (typeof issue.number !== "number") {
    throw new DispatchRefused("the issue could not be created", "GitHub returned no number");
  }
  return String(issue.number);
}

/**
 * Cancel the run the WEBHOOK started for the issue we just opened.
 *
 * **OPENING AN ISSUE ALREADY STARTS A RUN**, and that is the architecture working:
 * the Lambda verifies the HMAC, EventBridge matches `issues`/`opened`, and the rule
 * dispatches this same workflow. So creating an issue here and then dispatching
 * produces TWO runs against one issue.
 *
 * ── THE FIRST VERSION GUESSED, AND IT GUESSED THE WRONG WAY ──────────────────
 *
 * It cancelled "everything but the newest", asserting that ours would be the newest
 * because the issue is created first and our dispatch happens after. MEASURED on
 * issue #61, which produced two runs three seconds apart:
 *
 *     35363401644  created 15:35:57  trigger ui      <- OURS, and the OLDER one
 *     35363407186  created 15:36:00  trigger issue   <- the webhook's
 *
 * A direct POST beats a delivery through a Lambda, an event bus and an API
 * destination. So the rule was inverted: when it fired at all it would cancel the
 * APPLICATION's run and keep the webhook's — which hardcodes `poisoned: "false"`
 * and sends no `repo`, so a poisoned demo would have run clean with every job green.
 *
 * On #61 it cancelled nothing instead, because it looked once, immediately, and the
 * webhook's run did not exist yet. Both ran; the duplicate queued behind the first
 * on the concurrency group and started an hour later, which is why two rows for one
 * ticket appeared with timestamps an hour apart.
 *
 * ── WHAT IT DOES NOW ─────────────────────────────────────────────────────────
 *
 * `run-name` on the workflow puts the ticket and the trigger into `display_title`,
 * which `GET /actions/runs` DOES return — a run's inputs are not in that response,
 * which is why there was nothing to match on before. So this cancels the run titled
 * `#<ticket> (issue)` and nothing else: not by age, not by position, by name.
 *
 * **IT WAITS, BECAUSE THE RUN IT IS LOOKING FOR DOES NOT EXIST YET.** One look
 * immediately after dispatching is a look before the webhook has been delivered.
 *
 * BEST EFFORT, AND IT NEVER THROWS. A duplicate run is untidy; a failure here would
 * turn a started run into an error message for a run that IS running. Anything it
 * cannot cancel simply runs — `cancel-in-progress` is false on the concurrency
 * group, so the duplicate queues behind ours rather than racing it.
 */
const DUPLICATE_ATTEMPTS = 6;
const DUPLICATE_WAIT_MS = 4000;

async function cancelWebhookDuplicate(ticketId: string): Promise<void> {
  // EXACTLY WHAT THE WEBHOOK'S RUN IS CALLED. `modules/ingress`'s input transformer
  // sends `"trigger": "issue"`; this application sends `"ui"`. `tests/
  // test_trigger_provenance.py` asserts those two values DIFFER, because identical
  // ones would make the field prove nothing — and this is the first thing that
  // depends on the difference rather than merely recording it.
  const target = `#${ticketId} (issue)`;
  try {
    const token = await dispatchToken();
    const head = {
      authorization: `Bearer ${token}`,
      accept: "application/vnd.github+json",
      "user-agent": "theagentorg-web",
    };

    for (let attempt = 0; attempt < DUPLICATE_ATTEMPTS; attempt += 1) {
      const listed = await fetch(
        `https://api.github.com/repos/${PIPELINE_REPO}/actions/workflows/${WORKFLOW}/runs?per_page=20`,
        { headers: head, cache: "no-store" },
      );
      if (listed.ok) {
        const { workflow_runs: runs = [] } = (await listed.json()) as {
          workflow_runs?: { id: number; display_title?: string; status?: string }[];
        };
        const duplicate = runs.find(
          (r) => r.display_title === target && r.status !== "completed",
        );
        if (duplicate) {
          await fetch(
            `https://api.github.com/repos/${PIPELINE_REPO}/actions/runs/${duplicate.id}/cancel`,
            { method: "POST", headers: head },
          );
          return;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, DUPLICATE_WAIT_MS));
    }
  } catch {
    // Swallowed on purpose. See the docstring.
  }
}

/** Everything the workflow refuses, checked here so the message is ours. */
function refuseBadInput({ title }: RunRequest): void {
  const text = title.trim();
  if (text.length < 10) {
    throw new DispatchRefused(
      "say what the change should do, in a sentence",
      // MEASURED, and recorded in CLAUDE.md: a vague ticket legitimately ends
      // `status=failed` at the revision cap, because the reviewer keeps
      // withholding approval for a change that does not implement it.
      "a vague ticket ends at the revision cap with the reviewer refusing, which " +
        "is correct behaviour and looks like a broken pipeline",
    );
  }
  if (text.length > 200) {
    // The title becomes the ticket the agents read AND the issue's title, which
    // GitHub caps at 256.
    throw new DispatchRefused("that is too long for an issue title", "200 characters at most");
  }
}

/**
 * Dispatch the workflow. Returns nothing: GitHub answers 204 with no body and no
 * run id, so there is nothing honest to hand back.
 *
 * **THE RUN ID CANNOT BE RETURNED, AND PRETENDING OTHERWISE WOULD BE WORSE.**
 * `workflow_dispatch` is fire-and-forget; the run's id is minted by the `plan` job
 * and only reaches this application when `run_index.record_run` writes it. So the
 * caller polls the list rather than being handed an id that does not exist yet.
 */
export async function startRun(request: RunRequest): Promise<{ issue: string }> {
  refuseBadInput(request);
  const token = await dispatchToken();

  // THE ISSUE FIRST, because its number is the ticket id and every stage comment
  // lands on it. Creating it here is what removed the "issue number" field the
  // operator rightly objected to.
  // THE ISSUE AND THE WORKFLOW MUST NAME THE SAME REPOSITORY. Resolved once, here,
  // and passed to both: the issue is where every stage comment lands, and the
  // workflow input is what the agents change. Two resolutions would let a run
  // comment its plan onto one repository and open its pull request on another --
  // with every job green, because neither half can see the other's choice.
  const repo = request.repository?.trim() || TARGET_REPO;
  const ticketId = await createIssue(
    request.title.trim(),
    request.body?.trim()
      ? `${request.body.trim()}\n\n---\nOpened from The Agent Org.`
      : "Opened from The Agent Org.",
    repo,
  );

  const response = await fetch(
    `https://api.github.com/repos/${PIPELINE_REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        // The scheme belongs in the VALUE. CLAUDE.md records EventBridge sending
        // `Bearer: <token>` when the scheme was put in the key — GitHub ignores an
        // unrecognised auth header and answers **404, not 401**, which reads as
        // "the workflow does not exist".
        authorization: `Bearer ${token}`,
        accept: "application/vnd.github+json",
        "content-type": "application/json",
        "user-agent": "theagentorg-web",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: {
          // EVERY VALUE A STRING. `workflow_dispatch` inputs arrive as strings and
          // the REST dispatch API REJECTS real JSON booleans inside `inputs` —
          // which is why the ingress transformer quotes them too.
          ticket_id: ticketId,
          ticket_text: request.title.trim(),
          poisoned: request.poisoned ? "true" : "false",
          auto_approve: "false",
          trigger: "ui",
          // EVERY VALUE A STRING, this one included. Empty is impossible here --
          // `repo` is resolved above -- but the workflow's own default is `""` so
          // the ingress and a hand dispatch still fall through to the variable.
          repo,
        },
      }),
    },
  );

  if (response.status !== 204) {
    const body = await response.text().catch(() => "");
    if (response.status === 404) {
      // 404 HAS TWO CAUSES HERE AND NEITHER IS "not found". GitHub answers 404 for a
      // workflow file absent from the ref AND for an unauthenticated dispatch, so the
      // message must not send the next person looking for a missing file.
      throw new DispatchRefused(
        "the pipeline refused to start",
        "GitHub answered 404, which means either the workflow is not on `main` or " +
          "the dispatch token lacks `actions: write` on this repository",
      );
    }
    throw new DispatchRefused(
      "the pipeline refused to start",
      `GitHub answered ${response.status}: ${body.slice(0, 200)}`,
    );
  }

  // The webhook fired its own run when the issue opened. See the docstring.
  //
  // **NOT AWAITED, AND THAT IS THE POINT OF THE WAIT INSIDE IT.** It polls for up to
  // twenty seconds for a run that does not exist yet; blocking the response on that
  // would make "Start a run" feel broken for the whole window. The caller has what
  // it needs -- the issue number -- the moment the dispatch is accepted.
  void cancelWebhookDuplicate(ticketId);
  return { issue: ticketId };
}

/** For tests: drop the cached token. */
export function resetDispatchTokenCache(): void {
  cachedToken = null;
}

/**
 * The same read for several runs at once, for the list screen.
 *
 * **CAPPED, AND THE CAP IS STATED RATHER THAN HIDDEN.** Each run costs two GitHub
 * calls, so an unbounded list would make one page load a burst against the API and
 * turn a rate limit into a screen that fails to load. Past the cap a run keeps its
 * STORED status, which is the honest degradation: `running` on a finished run is
 * stale, and a fabricated ending would be wrong.
 *
 * Reconciling only the newest few is not arbitrary — rows arrive newest first, and
 * a run old enough to be past the cap is one nobody is watching.
 */
const RECONCILE_AT_MOST = 8;

export async function listProgress(ciRunIds: string[]): Promise<Record<string, CiProgress>> {
  const wanted = ciRunIds.filter(Boolean).slice(0, RECONCILE_AT_MOST);
  const answers = await Promise.all(wanted.map((id) => runProgress(id)));
  const out: Record<string, CiProgress> = {};
  wanted.forEach((id, i) => {
    const progress = answers[i];
    // An unreachable run is ABSENT from the map, not present-and-empty: the
    // reconciler reads a missing entry as "GitHub could not be asked" and leaves
    // the stored status alone, where an empty `jobs` record would read as a run
    // whose every job is missing and resolve to `failed`.
    if (progress) out[id] = progress;
  });
  return out;
}

/** What GitHub says about one job of a pipeline run. Its own words, unmapped. */
export type CiJob = {
  /** `queued` · `waiting` (held by an Environment) · `in_progress` · `completed`. */
  status: string;
  /** `success` · `failure` · `skipped` · `cancelled`, and `""` until it completes. */
  conclusion: string;
};

/** GitHub's own view of a run: what is moving, and what waits for a person. */
export type CiProgress = {
  status: string;
  conclusion: string;
  jobs: Record<string, CiJob>;
  /** Environment names with a deployment waiting for a reviewer, right now. */
  awaiting: string[];
};

/**
 * READ THE PIPELINE FROM GITHUB, because GitHub is what the pipeline IS.
 *
 * ── THE DEFECT THIS EXISTS TO CLOSE ──────────────────────────────────────────
 *
 * **A GATE JOB HAS NO AWS CREDENTIALS, SO A GATE DECISION CANNOT REACH THE INDEX
 * WHEN IT IS MADE.** `run_stage._emit` calls `run_index.update_status` at every
 * stage, and that write needs a credential the gate jobs deliberately do not hold
 * (`test_no_gate_job_can_reach_aws_or_run_an_agent` pins it: "a pause needs no
 * credentials"). `record_run` swallows the failure and never raises — correct, an
 * index is not the run's record — so the decision lands in the index only when the
 * NEXT credentialled job rewrites the whole state document.
 *
 * MEASURED on run 35057681679, every job `success` and the run `completed`:
 *
 *     stored index row     status running, decisions [gate1, gate2]
 *     GitHub               gate3 success, promote success, run completed
 *
 * | decision | recorded by | reaches the index via | lag      |
 * |----------|-------------|-----------------------|----------|
 * | gate1    | `gate1`     | `develop`             | seconds  |
 * | gate2    | `gate2`     | `sre`                 | ~2 min   |
 * | gate3    | `gate3`     | `promote` — no AWS    | NEVER    |
 *
 * Both symptoms reported from the deployed app are that one table: *"when I
 * approve gate 2 it works but it takes 2 min"* is the middle row, and *"when I
 * approve gate3 nothing happened"* is the last. **Neither approval failed.** The
 * screen was reading a record that had not been told.
 *
 * ── WHY THE FIX IS A READ AND NOT A RETRY ────────────────────────────────────
 *
 * The index is a DERIVED copy; the Environment and the jobs are the thing itself.
 * A gate is released by `POST .../pending_deployments` and by nothing else, so the
 * question "is this run waiting on a person" has exactly one authority. Deriving
 * the screen from the copy and the decision from the original is what let a button
 * appear for a gate the server then refused — recorded in `runDetail`, and this is
 * the same mistake from the other end.
 *
 * **NULL ON ANY FAILURE, NEVER A THROW.** A GitHub outage must leave the run
 * readable: the stored document is still a true record of everything the run did,
 * and losing the whole screen because the live overlay is unavailable would be
 * worse than showing it without the overlay. The caller falls back to the document
 * and the page says which it is showing.
 */
export async function runProgress(ciRunId: string): Promise<CiProgress | null> {
  if (!/^[0-9]{1,20}$/.test(ciRunId)) return null;
  try {
    const token = await dispatchToken();
    const head = {
      authorization: `Bearer ${token}`,
      accept: "application/vnd.github+json",
      "user-agent": "theagentorg-web",
    };
    const at = `https://api.github.com/repos/${PIPELINE_REPO}/actions/runs/${ciRunId}`;

    // TWO CALLS FOR AN ENDED RUN, THREE FOR A LIVE ONE. `pending_deployments` is
    // only asked when something could still be waiting — a completed run has no
    // pending deployment by definition, and this page polls every five seconds.
    const [runRes, jobsRes] = await Promise.all([
      fetch(at, { headers: head, cache: "no-store" }),
      fetch(`${at}/jobs?per_page=50`, { headers: head, cache: "no-store" }),
    ]);
    if (!runRes.ok || !jobsRes.ok) return null;

    const run = (await runRes.json()) as { status?: string; conclusion?: string | null };
    const { jobs = [] } = (await jobsRes.json()) as {
      jobs?: { name?: string; status?: string; conclusion?: string | null }[];
    };

    const byName: Record<string, CiJob> = {};
    for (const job of jobs) {
      if (!job.name) continue;
      byName[job.name] = {
        status: String(job.status ?? ""),
        conclusion: String(job.conclusion ?? ""),
      };
    }

    const status = String(run.status ?? "");
    let awaiting: string[] = [];
    if (status !== "completed") {
      const pending = await fetch(`${at}/pending_deployments`, {
        headers: head,
        cache: "no-store",
      });
      if (pending.ok) {
        const waiting = (await pending.json()) as { environment?: { name?: string } }[];
        awaiting = waiting.map((w) => String(w.environment?.name ?? "")).filter(Boolean);
      }
    }

    return { status, conclusion: String(run.conclusion ?? ""), jobs: byName, awaiting };
  } catch {
    // Deliberately blind, and deliberately silent about which failure it was: the
    // caller's only decision is overlay-or-document, and three causes (no token, a
    // 404, a network fault) all answer it the same way.
    return null;
  }
}

/**
 * Release a GitHub Environment gate, which is what a gate on this pipeline IS.
 *
 * **THE APPROVAL BUTTON WROTE TO THE WRONG PLACE.** `web/lib/reader/approve.py` calls
 * `queue.resume`, which makes a paused QUEUE job claimable. The deployed pipeline does
 * not use the queue: its gates are GitHub Environments with required reviewers, and a
 * job waiting on one is released by
 * `POST /repos/{repo}/actions/runs/{id}/pending_deployments` and by nothing else. So
 * the button recorded a decision in a queue nobody was reading and the run stayed
 * waiting.
 *
 * **THE ACTIONS RUN ID IS A DIFFERENT NUMBER FROM `run_id`**, which is why
 * `run_index` now writes `ci_run_id` onto the index row. `run_id` is the pipeline's
 * uuid4; this is GitHub's, and without it this application can SEE that a run waits
 * for a person and cannot tell GitHub who decided.
 *
 * ── THE ATTRIBUTION GAP, STATED RATHER THAN HIDDEN ───────────────────────────
 *
 * **GITHUB WILL RECORD THE TOKEN'S OWNER AS THE APPROVER, NOT THE PERSON WHO
 * CLICKED.** There is no way around that: the REST call authenticates as the token,
 * and an Environment approval is attributed to the authenticated user. This
 * repository already paid for the inverse mistake — a rejection recorder posted
 * `REJECTED by mohamedsorour1998` naming a human who never saw the gate, and
 * CLAUDE.md calls fabricating a decision against a person's name "the inverse of the
 * defect this job exists to prevent".
 *
 * So the caller's identity is put in the approval COMMENT, where GitHub preserves it
 * verbatim, and `recordDecision` returns what was actually recorded rather than
 * echoing the session. Two names appear, and neither is invented: the reviewer who
 * clicked, and the token that transmitted it.
 */
export async function approveGate(
  ciRunId: string,
  gate: string,
  decision: "approved" | "rejected",
  by: string,
  reason: string,
): Promise<{ environment: string; approvedAs: string }> {
  const token = await dispatchToken();
  const head = {
    authorization: `Bearer ${token}`,
    accept: "application/vnd.github+json",
    "user-agent": "theagentorg-web",
  };

  const pending = await fetch(
    `https://api.github.com/repos/${PIPELINE_REPO}/actions/runs/${ciRunId}/pending_deployments`,
    { headers: head },
  );
  if (!pending.ok) {
    throw new DispatchRefused(
      "that gate could not be reached",
      `GitHub answered ${pending.status} for run ${ciRunId}`,
    );
  }
  const waiting = (await pending.json()) as {
    environment?: { id?: number; name?: string };
    current_user_can_approve?: boolean;
  }[];

  const match = waiting.find((w) => w.environment?.name === gate);
  if (!match?.environment?.id) {
    // NAMES WHAT IS ACTUALLY WAITING. "no such gate" would be wrong when the real
    // answer is that this run is waiting at a DIFFERENT gate, or at none.
    const names = waiting.map((w) => w.environment?.name).filter(Boolean);
    throw new DispatchRefused(
      "that gate is not waiting for a decision",
      names.length
        ? `this run is waiting at ${names.join(", ")}`
        : "this run is not paused at any gate",
    );
  }
  if (match.current_user_can_approve === false) {
    // THE TOKEN IS NOT A REQUIRED REVIEWER. An Environment only accepts approvals
    // from the people it names, which is the property that makes a gate a gate.
    throw new DispatchRefused(
      "this deployment cannot approve that gate",
      `the dispatch token's owner is not a required reviewer on ${gate}`,
    );
  }

  const sent = await fetch(
    `https://api.github.com/repos/${PIPELINE_REPO}/actions/runs/${ciRunId}/pending_deployments`,
    {
      method: "POST",
      headers: { ...head, "content-type": "application/json" },
      body: JSON.stringify({
        environment_ids: [match.environment.id],
        state: decision === "approved" ? "approved" : "rejected",
        // THE CLICKER'S NAME TRAVELS HERE, because GitHub attributes the approval
        // itself to the token. See the attribution note above.
        comment: `${decision} by ${by} in The Agent Org${reason ? `: ${reason}` : ""}`.slice(0, 500),
      }),
    },
  );
  if (!sent.ok) {
    const text = await sent.text().catch(() => "");
    throw new DispatchRefused(
      "the decision was not recorded",
      `GitHub answered ${sent.status}: ${text.slice(0, 160)}`,
    );
  }
  return { environment: gate, approvedAs: by };
}
