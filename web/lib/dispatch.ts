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
async function createIssue(title: string, body: string): Promise<string> {
  const token = await dispatchToken();
  const response = await fetch(`https://api.github.com/repos/${TARGET_REPO}/issues`, {
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
      `GitHub answered ${response.status} for ${TARGET_REPO}: ${text.slice(0, 160)}`,
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
 * produces TWO runs against one issue — which CLAUDE.md records happening during
 * rehearsal, leaving three plan comments on one issue and reading as a loop.
 *
 * **OURS IS THE NEWER ONE, WHICH IS WHAT MAKES THIS DETERMINISTIC.** The issue is
 * created first, the webhook fires within seconds, and our dispatch happens after —
 * so any `run-pipeline.yml` run created between the issue and our dispatch is the
 * auto-run. Nothing else distinguishes them: EventBridge dispatches through the same
 * REST API `gh workflow run` uses, so both report `event: workflow_dispatch`, which
 * is the whole reason `RunState.trigger` exists.
 *
 * BEST EFFORT, AND IT NEVER THROWS. A duplicate run is untidy; a sign-up-style
 * failure here would turn a started run into an error message for a run that IS
 * running. `cancel-in-progress` is false on the workflow's concurrency group, so the
 * duplicate would otherwise sit queued behind ours rather than racing it.
 */
async function cancelWebhookDuplicate(since: string, ours: string): Promise<void> {
  try {
    const token = await dispatchToken();
    const listed = await fetch(
      `https://api.github.com/repos/${PIPELINE_REPO}/actions/workflows/${WORKFLOW}/runs?per_page=10`,
      {
        headers: {
          authorization: `Bearer ${token}`,
          accept: "application/vnd.github+json",
          "user-agent": "theagentorg-web",
        },
      },
    );
    if (!listed.ok) return;
    const { workflow_runs: runs = [] } = (await listed.json()) as {
      workflow_runs?: { id: number; created_at: string; status: string }[];
    };
    const candidates = runs
      .filter((r) => r.created_at >= since && r.status !== "completed")
      .sort((a, b) => a.created_at.localeCompare(b.created_at));
    // Everything but the newest: the newest is the dispatch this request made.
    for (const run of candidates.slice(0, -1)) {
      await fetch(`https://api.github.com/repos/${PIPELINE_REPO}/actions/runs/${run.id}/cancel`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${token}`,
          accept: "application/vnd.github+json",
          "user-agent": "theagentorg-web",
        },
      });
    }
  } catch {
    // Swallowed on purpose. See the docstring.
  }
  void ours;
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
  const since = new Date(Date.now() - 5_000).toISOString();
  const ticketId = await createIssue(
    request.title.trim(),
    request.body?.trim()
      ? `${request.body.trim()}\n\n---\nOpened from The Agent Org.`
      : "Opened from The Agent Org.",
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
  await cancelWebhookDuplicate(since, ticketId);
  return { issue: ticketId };
}

/** For tests: drop the cached token. */
export function resetDispatchTokenCache(): void {
  cachedToken = null;
}
