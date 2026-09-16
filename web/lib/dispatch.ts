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

export type RunRequest = {
  /** The issue number on the TARGET repository. Bare digits. */
  ticketId: string;
  ticketText: string;
  /** Seed the change with a committed credential, to demonstrate the block. */
  poisoned: boolean;
};

/** Everything the workflow refuses, checked here so the message is ours. */
function refuseBadInput({ ticketId, ticketText }: RunRequest): void {
  if (!/^[0-9]+$/.test(ticketId)) {
    throw new DispatchRefused(
      "the ticket must be an issue number",
      "a run whose ticket is not a bare number still executes, but every stage " +
        "comment goes nowhere and the issue is never updated",
    );
  }
  const text = ticketText.trim();
  if (text.length < 10) {
    throw new DispatchRefused(
      "the ticket needs a sentence the planner can work from",
      // MEASURED, and recorded in CLAUDE.md: a vague ticket legitimately ends
      // `status=failed` at the revision cap, because the reviewer keeps
      // withholding approval for a change that does not implement it.
      "a vague ticket ends at the revision cap with the reviewer refusing, which " +
        "is correct behaviour and looks like a broken pipeline",
    );
  }
  if (text.length > 2000) {
    throw new DispatchRefused("that ticket text is too long", "2000 characters at most");
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
export async function startRun(request: RunRequest): Promise<void> {
  refuseBadInput(request);
  const token = await dispatchToken();

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
          ticket_id: request.ticketId,
          ticket_text: request.ticketText.trim(),
          poisoned: request.poisoned ? "true" : "false",
          auto_approve: "false",
          trigger: "ui",
        },
      }),
    },
  );

  if (response.status === 204) return;

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

/** For tests: drop the cached token. */
export function resetDispatchTokenCache(): void {
  cachedToken = null;
}
