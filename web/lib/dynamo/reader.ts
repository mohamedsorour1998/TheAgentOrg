/**
 * The tenancy reads, in TypeScript, against DynamoDB. Replaces the Python
 * subprocesses in `web/lib/reader/` for the deployed runtime.
 *
 * **WHY THIS EXISTS.** Measured 2026-09-15 against the deployed app, signed in as a
 * real reviewer whose session carried `tenant_id: tenant-zero`:
 *
 *     /api/runs         -> PipelineError: the pipeline reader could not be started
 *     /api/repositories -> PipelineError: the pipeline reader could not be started
 *
 * `web/lib/pipeline.ts` spawned `.venv-main/bin/python`, and an Amplify SSR Lambda has
 * no Python, no virtualenv and no repository checkout. The Python readers were correct,
 * tested, tenant-scoped, and **unable to start** -- this repository's signature pattern
 * at its largest scale. Sign-in worked; every screen behind it was an error.
 *
 * **THE SCOPING IS UNCHANGED, AND THAT IS THE WHOLE ARGUMENT.** Every query here runs on
 * a credential minted by `scopedClient`, which assumes the tenancy role with the tenant
 * as an IAM session tag. AWS compares that tag against `dynamodb:LeadingKeys`. This
 * module cannot widen its own scope by writing a different `WHERE`, because there is no
 * `WHERE` -- a wrong partition key returns AccessDenied from AWS, not rows.
 *
 * That is why CLAUDE.md's ruling against a Node client does not apply here. It was about
 * SQLite triggers comparing against `current_tenant()`, a function only the Python
 * engine registers, where a Node client failed every WRITE loudly and succeeded at every
 * READ *unscoped*. With DynamoDB the enforcement moved into the credential.
 *
 * THE THREE-STATE `indexed` DISTINCTION IS PRESERVED, because collapsing it is the
 * "did not run versus passed" defect:
 *
 *     TENANCY_TABLE unset        -> indexed: false, empty list. Nothing indexes runs.
 *     set, no rows               -> indexed: true, empty list. This tenant has had none.
 *     set, rows                  -> indexed: true, the rows.
 */

import { QueryCommand, PutCommand, GetCommand, DeleteCommand } from "@aws-sdk/lib-dynamodb";
import { randomUUID } from "node:crypto";

import { scopedClient } from "./credentials";
import { SK_REPO, SK_RUN, isSafeRunId, sk, tenantForRunState, tenantPk } from "./keys";
// PURE, so the translation every viewer sees can be driven by a test without a
// table or a token. `dispatch` itself is imported lazily at the call sites, because
// it reaches Secrets Manager at module scope's expense and the read path must not
// pay for it when there is no `ci_run_id` to ask about.
import { gatesAwaiting, reconcileStatus, stagesFromCi } from "../ci-view";
// TYPE ONLY, so nothing from `dispatch` is pulled in at module scope -- it opens a
// Secrets Manager client, and this module is imported by every read.
import type { CiProgress } from "../dispatch";

/**
 * The table, read with NO DEFAULT. Blank means "this deployment does not index runs",
 * which is a legitimate state and not an error -- the same gate
 * `agentorg/tenancy/run_index.py` reads, by the same name. A default here would make
 * "not configured" unreachable and collapse the three states above into two.
 */
function indexTableName(): string {
  return (process.env.TENANCY_TABLE ?? "").trim();
}

export class ReadRefused extends Error {
  constructor(
    message: string,
    readonly detail = "",
  ) {
    super(message);
  }
}

type Row = Record<string, unknown>;

/** Every row of one type in one tenant's partition, following pagination. */
async function rowsOfType(tenantId: string, token: string): Promise<Row[]> {
  const table = indexTableName();
  const client = await scopedClient(tenantId);
  const out: Row[] = [];
  let startKey: Record<string, unknown> | undefined;

  do {
    const answer = await client.send(
      new QueryCommand({
        TableName: table,
        KeyConditionExpression: "pk = :pk AND begins_with(sk, :sk)",
        ExpressionAttributeValues: { ":pk": tenantPk(tenantId), ":sk": `${token}#` },
        ExclusiveStartKey: startKey,
      }),
    );
    out.push(...((answer.Items ?? []) as Row[]));
    // FOLLOWED, NOT IGNORED. A single page is the defect that makes a tenant with
    // many runs silently show a prefix of them, and it looks identical to a tenant
    // who has that many.
    startKey = answer.LastEvaluatedKey as Record<string, unknown> | undefined;
  } while (startKey);

  return out;
}

/** `https://github.com/owner/name/pull/61` -> `owner/name`. */
const PULL_REQUEST = /^https:\/\/github\.com\/([^/\s]+\/[^/\s]+)\/pull\/\d+/;

/**
 * WHICH REPOSITORY A RUN ACTED ON.
 *
 * `agentorg/state.py` is frozen and declares no repository field, so there are two
 * honest sources and no third: the column `run_index.record_run` writes, and the
 * pull request's own URL for rows written before that column existed.
 *
 * **IT DOES NOT FALL BACK TO "the tenant's only repository".** That guess is right
 * today, because one repository is in scope, and it becomes wrong silently the
 * moment a second is added -- displaying a change as having been made somewhere it
 * was not. `""` renders as "not recorded", which is true.
 */
function repositoryOf(row: Row, state: RunStateDoc | null): string {
  const declared = String(row.repository ?? "").trim();
  if (declared) return declared;
  const pr = (state?.dev as Record<string, unknown> | undefined)?.pr_url;
  const matched = typeof pr === "string" ? PULL_REQUEST.exec(pr) : null;
  return matched?.[1] ?? "";
}

/**
 * The row as the LIST needs it.
 *
 * **THE LAST THREE FIELDS WERE HARDCODED NULL AND THE REASON HAD EXPIRED.** The
 * comment here said they "come from the run's STATE DOCUMENT, which lives in
 * `theagentorg-runs` and is only written when the pipeline runs on
 * STATE_BACKEND=dynamodb" -- true when written, and obsolete the moment
 * `run_index` began denormalising the whole document onto THIS row. So every run
 * in the list rendered `NOT SCANNED · not scanned · PROVENANCE UNKNOWN`, including
 * runs that were scanned by three real scanners and promoted.
 *
 * That is the worst possible direction for this particular screen: the product's
 * entire claim is that a deterministic rule reads real scanner output, and the
 * list was reporting that no scan had happened, for runs where it had. Measured on
 * the live row for run f8f67ab3: `verdict='pass'`, `blocking=0`,
 * `scan_provenance='scanners'` -- all three present and all three discarded.
 *
 * **IT IS THE SAME EXPIRED PREMISE AS `awaiting_gates: []`**, which made the
 * approve button unreachable. A comment that justifies a constant outlives the
 * fact that justified it, and reads as a decision rather than as a stale one.
 */
function summarise(row: Row, progress: CiProgress | null = null) {
  const state = stateOf(row);
  const security = (state?.security ?? null) as Record<string, unknown> | null;
  const status = reconcileStatus(
    String(state?.status ?? row.status ?? "running"),
    progress,
    state,
  );

  return {
    run_id: String(row.run_id ?? ""),
    ticket_id: String(row.ticket_id ?? ""),
    status,
    created_at: String(row.created_at ?? ""),
    // `null` STILL MEANS NOT SCANNED, and now it means it truthfully: the security
    // stage has not produced a verdict rather than the reader having declined to
    // look. A run at `plan` genuinely has none.
    verdict: (security?.verdict as string) ?? null,
    // `""` MEANS NOBODY RECORDED PROVENANCE -- a row written before the field
    // existed. The UI renders it as unknown, which is distinct from `scanners`
    // (a real scan) and from `fixture-fallback` (a scanner failed and the fixture
    // stood in). Collapsing those hides a broken gate behind a demo setting.
    scan_provenance: (security?.scan_provenance as string) ?? "",
    blocking: Array.isArray(security?.blocking) ? security.blocking.length : null,
    // ONE GATE AT MOST, and from GitHub when GitHub could be asked -- the same
    // source the detail screen and the approval route use, so a run cannot read as
    // waiting in the list and not waiting when it is opened.
    awaiting_gate: (progress ? gatesAwaiting(progress) : awaitingGates(state, status))[0] ?? "",
    repository: repositoryOf(row, state),
  };
}

/**
 * The runs this tenant owns, newest first, with any stale `running` corrected.
 *
 * **THE LIST HAD THE SAME DEFECT AS THE DETAIL SCREEN AND IT LOOKS WORSE HERE.** A
 * run that merged an hour ago sat in this list as `RUNNING` forever, because the
 * job that ends a run (`promote`) holds no AWS credential and so cannot write its
 * own ending — see `lib/dispatch.ts:runProgress`. Three finished runs reading as
 * three live ones is not a cosmetic lag: it is the list telling somebody work is
 * still going on.
 *
 * **ONE GITHUB CALL FOR THE WHOLE PAGE, not one per run.** `listProgress` reads the
 * workflow's recent runs once and matches on `ci_run_id`. A per-run read would be
 * N round trips on a screen that is polled, for a correction that is the same shape
 * for every row — and it would make an unreachable GitHub N failures instead of one.
 */
async function listRuns(tenantId: string) {
  if (!indexTableName()) return { runs: [], indexed: false };
  const rows = await rowsOfType(tenantId, SK_RUN);
  rows.sort((a, b) => String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")));

  // ONLY IF SOMETHING CLAIMS TO BE RUNNING. A page of finished runs asks GitHub
  // nothing, which is the common case once a demo is over.
  const unfinished = rows.filter((row) => String(row.status ?? "running") === "running");
  let conclusions: Record<string, CiProgress> = {};
  if (unfinished.length > 0) {
    const { listProgress } = await import("../dispatch");
    conclusions = await listProgress(
      unfinished.map((row) => String(row.ci_run_id ?? "")).filter(Boolean),
    );
  }

  return {
    // The same reconciler the detail screen uses, so a run cannot read as ended in
    // one place and running in the other. Two derivations of one fact is how a
    // list and a detail page start disagreeing about the same run.
    runs: rows.map((row) => summarise(row, conclusions[String(row.ci_run_id ?? "")] ?? null)),
    indexed: true,
  };
}

async function listRepositories(tenantId: string) {
  if (!indexTableName()) return { repositories: [], indexed: false };
  const rows = await rowsOfType(tenantId, SK_REPO);
  return {
    repositories: rows
      .map((r) => ({ full_name: String(r.full_name ?? "") }))
      .filter((r) => r.full_name),
    indexed: true,
  };
}

/**
 * REPLACE this tenant's scope with exactly `fullNames`, then READ THE RESULT BACK.
 *
 * **IT ONLY ADDED, AND THAT WAS THE BUG.** Reported: a repository was unticked,
 * "Save scope" reported *"Scope saved."*, and it came back ticked. This function
 * looped the wanted names, skipped the ones already present, wrote the rest -- and
 * never removed a row for a repository that was no longer wanted. So unticking
 * anything did nothing, for every repository, and the screen said it had worked.
 *
 * Every layer around it called this a REPLACE: the route is a `PUT`, the screen
 * says "Saving replaces the set, which means unticking a repository removes it",
 * and `RepositoryScopeRequest` carries the whole set rather than a delta. Only the
 * write itself was an ADD, and its own docstring said so in the first line -- the
 * one place the contradiction was visible was the place nobody re-read.
 *
 * **THE SCOPE IS AN AUTHORISATION BOUNDARY**, which is what makes an add-only
 * write the dangerous direction: `authz.decide` permits an approval when the run's
 * repository is in this list, so a repository nobody could remove is a permission
 * nobody could revoke.
 *
 * Reading the result back is unchanged and still the point: echoing the request
 * would reassure a caller that a refused write had landed.
 */
async function setScope(tenantId: string, fullNames: string[], by: string) {
  if (!indexTableName()) return { repositories: [], indexed: false };
  if (!by.trim()) {
    // An authorisation boundary changing with nobody's name on it is the same defect
    // as a gate decision with a constant `by`.
    throw new ReadRefused("a scope change needs the identity of the person making it");
  }

  // REVALIDATED HERE, not trusted from the route. The next thing these values do is
  // become rows the approval check reads.
  for (const name of fullNames) {
    const parts = name.split("/");
    if (parts.length !== 2 || parts.some((p) => !p) || /[\\ \t\n\r\0]/.test(name)) {
      throw new ReadRefused("every entry must be of the form owner/name");
    }
  }

  const table = indexTableName();
  const client = await scopedClient(tenantId);
  const existing = new Set(
    (await rowsOfType(tenantId, SK_REPO)).map((r) => String(r.full_name ?? "")),
  );

  const wanted = new Set(fullNames);

  // REMOVE FIRST. If a later add fails, the tenant is left with a SMALLER scope
  // than they asked for, never a larger one -- and for a list that decides what an
  // approval may touch, the safe direction to fail in is fewer permissions.
  for (const row of await rowsOfType(tenantId, SK_REPO)) {
    const name = String(row.full_name ?? "");
    if (wanted.has(name)) continue;
    await client.send(
      new DeleteCommand({
        TableName: table,
        // The row's OWN sort key. A repository row is keyed on a random id rather
        // than on its name, so the key cannot be rebuilt from `full_name` -- it has
        // to come off the row that was just read.
        Key: { pk: tenantPk(tenantId), sk: String(row.sk ?? "") },
      }),
    );
    existing.delete(name);
  }

  for (const fullName of fullNames) {
    if (existing.has(fullName)) continue;
    const id = randomUUID();
    await client.send(
      new PutCommand({
        TableName: table,
        Item: {
          pk: tenantPk(tenantId),
          sk: sk(SK_REPO, id),
          id,
          tenant_id: tenantId,
          full_name: fullName,
          created_at: new Date().toISOString(),
        },
      }),
    );
  }

  return { ...(await listRepositories(tenantId)), changed_at: new Date().toISOString() };
}

/** One run's index row, or a refusal indistinguishable from "no such run". */
async function requireRun(tenantId: string, runId: string): Promise<Row> {
  const client = await scopedClient(tenantId);
  const answer = await client.send(
    new GetCommand({
      TableName: indexTableName(),
      Key: { pk: tenantPk(tenantId), sk: sk(SK_RUN, runId) },
    }),
  );
  if (!answer.Item) throw new ReadRefused("no such run");
  return answer.Item as Row;
}

/** The run's own record, denormalised onto the index row by `run_index`. */
type RunStateDoc = Record<string, unknown>;

function stateOf(row: Row): RunStateDoc | null {
  const raw = row.state;
  if (typeof raw !== "string" || !raw) return null;
  try {
    return JSON.parse(raw) as RunStateDoc;
  } catch {
    // A DOCUMENT THAT WILL NOT PARSE IS `null`, NOT A THROW. The index row is still
    // a true fact about the run, and losing the whole screen because one field is
    // malformed would be worse than showing the run without its stages.
    return null;
  }
}

/**
 * The nine stages, derived from WHICH RESULTS THE RUN ACTUALLY HOLDS.
 *
 * **ABSENT MEANS NOT STARTED, AND PRESENT MEANS DONE. NOTHING IS INVENTED.** The
 * pipeline's own `StageView` carries `attempt`, `exit_code` and `enqueued_at`, which
 * are QUEUE facts -- and a run on the GitHub Actions path never enters the queue, so
 * this cannot know them. They are reported as one attempt, no exit code and no
 * timestamps rather than as plausible-looking numbers: a fabricated `exit_code: 0`
 * would be the one field on this screen that could contradict the run itself.
 *
 * A stage the run has not reached is OMITTED, which the spine renders as not started
 * -- correct, and the only honest answer.
 */
function stagesFrom(state: RunStateDoc, row: Row): unknown[] {
  const decisions = Array.isArray(state.decisions) ? (state.decisions as Record<string, unknown>[]) : [];
  const decided = new Set(decisions.map((d) => String(d.gate ?? "")));
  const status = String(state.status ?? row.status ?? "running");
  const ended = ["blocked", "rejected", "failed", "promoted"].includes(status);

  // Each stage, and the field on the RunState that proves it ran.
  const proof: [string, boolean][] = [
    ["plan", state.plan != null],
    ["gate1", decided.has("gate1")],
    ["develop", state.dev != null],
    ["review", state.review != null],
    ["security", state.security != null],
    ["gate2", decided.has("gate2")],
    ["sre", state.sre != null],
    ["gate3", decided.has("gate3")],
    ["promote", status === "promoted"],
  ];

  const out: unknown[] = [];
  for (const [stage, ran] of proof) {
    if (!ran) {
      // THE FIRST UNREACHED GATE OF A LIVE RUN IS `paused`, not absent: that is a
      // person being waited on, and the spine lifts it. A run that has ENDED is
      // waiting for nobody, so nothing after its ending is marked paused.
      const isGate = stage === "gate1" || stage === "gate2" || stage === "gate3";
      if (isGate && !ended && out.length > 0) {
        out.push({
          stage, status: "paused", attempt: 1, exit_code: null,
          enqueued_at: "", updated_at: "", reclaimed_from: "",
        });
      }
      break;
    }
    out.push({
      stage, status: "done", attempt: 1, exit_code: null,
      enqueued_at: String(state.started_at ?? row.created_at ?? ""),
      updated_at: "", reclaimed_from: "",
    });
  }
  return out;
}

/**
 * Everything one run's detail screen needs.
 *
 * OWNERSHIP FIRST, and it is the only check there is -- `requireRun` reads through the
 * TENANT-SCOPED credential, so a caller who does not own the run gets a refusal from a
 * credential that physically cannot see another tenant's index row.
 *
 * **THE FIELDS BELOW USED TO BE BLANK ON EVERY RUN**, because the run's record lives
 * in an Actions artifact this runtime cannot read. `run_index` now writes a copy onto
 * the index row, so the screen shows what the run actually did instead of rendering a
 * succeeded `plan` as `NOT STARTED`.
 */
async function runDetail(tenantId: string, runId: string) {
  if (!indexTableName()) throw new ReadRefused("no run index is configured");
  const row = await requireRun(tenantId, runId);
  const state = stateOf(row);
  const security = (state?.security ?? null) as Record<string, unknown> | null;
  const dev = (state?.dev ?? null) as Record<string, unknown> | null;

  /**
   * THE LIVE OVERLAY. GitHub answers what is happening; the document answers what
   * the run produced, and the two below never cross.
   *
   * **THE DOCUMENT CANNOT ANSWER THE FIRST QUESTION, AND THAT IS STRUCTURAL.** A
   * gate job holds no AWS credential — by design — so a gate decision reaches this
   * row only when the next credentialled job rewrites it. `gate3`'s next job is
   * `promote`, which holds none either, so a run that merged stays `running` here
   * forever. Measured on run 35057681679: every job `success`, every field on this
   * row still saying the run waits at gate3. `lib/dispatch.ts:runProgress` carries
   * the full table.
   *
   * The fallbacks below are not dead code: a run with no `ci_run_id`, and a run
   * read while GitHub is unavailable, both still render from the document alone.
   */
  // THE IMPORT IS INSIDE THE BRANCH, not above it: `dispatch` opens a Secrets
  // Manager client, and a run with no Actions page must not pay for one.
  const ciRunId = typeof row.ci_run_id === "string" ? row.ci_run_id : "";
  const progress = ciRunId ? await (await import("../dispatch")).runProgress(ciRunId) : null;

  return {
    // `summarise` ALREADY READS THE DOCUMENT AND RECONCILES THE STATUS, so the
    // verdict, the provenance, the blocking count, the repository and the status
    // are not restated here. They were, until the list was fixed -- and two
    // derivations of one fact is how a row and the page it opens start disagreeing.
    ...summarise(row, progress),
    ticket_text: String(state?.ticket_text ?? ""),
    model_provenance: String(state?.model_provenance ?? ""),
    trigger: String(state?.trigger ?? ""),
    poisoned: state?.poisoned === true,
    pr_url: (dev?.pr_url as string) ?? null,
    branch: (dev?.branch as string) ?? null,
    /**
     * FROM THE JOBS WHERE THERE ARE JOBS. `stagesFromCi` reads the run's seven
     * jobs and the two results that have none (`review` and `security` live inside
     * `develop`), so a stage in flight renders as running rather than as absent —
     * which is what the spinner on the spine is driven by, and what the document
     * structurally cannot say, since it is written only once a stage FINISHES.
     */
    stages: progress ? stagesFromCi(progress, state) : state ? stagesFrom(state, row) : [],
    decisions: Array.isArray(state?.decisions) ? state.decisions : [],
    security,

    // **WHAT THE AGENTS ACTUALLY SAID, which this screen did not show.** Reported
    // from the deployed app: *"i dont understand how it is running and we have no
    // info"*. The screen carried a status, a spine and a security verdict, so it
    // could say a run had planned and developed while showing nothing either agent
    // produced -- five agents' work rendered as five words.
    //
    // Every one of these was already on the row. `run_index` denormalises the whole
    // state document, so this is a projection and not a new read: the plan's tasks
    // and acceptance criteria, the diff the developer wrote, the reviewer's verdict
    // and its `must_fix` list, and the SRE's measured CI plus its advisory checks.
    //
    // `null` IS KEPT DISTINCT FROM AN EMPTY OBJECT, the way `scan_provenance` keeps
    // `""` distinct from `scanners`: `null` means the stage has not run, `{}` would
    // mean it ran and produced nothing. A screen that renders those the same way
    // tells somebody their reviewer had no objections when the reviewer never ran.
    /**
     * THE GITHUB ACTIONS RUN, so a person can go and watch the thing itself.
     *
     * Asked for directly: *"i need link of the run to be visible for user to click
     * on it"*. It was already on the row — `approveRun` reads it to find the
     * Environment to release — and no screen showed it, so the one place where the
     * jobs, the logs and the live progress actually live was reachable only by
     * somebody who already knew the URL.
     *
     * `""` WHERE THERE IS NONE, never a fabricated link. A run indexed before
     * `ci_run_id` was recorded, or one executed anywhere but Actions, genuinely has
     * no page to open — and a link that 404s is worse than no link, because it
     * reads as the run having been deleted.
     */
    // THE WHOLE URL, BUILT HERE. The repository name lives in `PIPELINE_REPO`, a
    // server-side value; handing the browser a bare id and asking it to know the
    // owner/name would put that constant in two places, and the copy on the client
    // would be the one that goes stale when the pipeline moves.
    ci_run_id: typeof row.ci_run_id === "string" && row.ci_run_id
      ? `https://github.com/${process.env.PIPELINE_REPO ?? "mohamedsorour1998/TheAgentOrg"}/actions/runs/${row.ci_run_id}`
      : "",
    /**
     * THE ISSUE, which is the other half of the run's public record.
     *
     * The ticket id IS the issue number -- `github_ops.post_comment` refuses a
     * non-numeric one and CLAUDE.md records what happens then: every stage comment
     * goes *nowhere, silently, while every job stays green*. So the plan, the gate
     * decisions and the outcome are written on the issue, while the diff, the
     * review and the security verdict are on the pull request. Linking only to the
     * second sent people to half of it.
     *
     * BUILT ONLY WHEN BOTH HALVES ARE KNOWN. A numeric ticket on a repository we
     * cannot name would produce a link to somebody else's issue of that number,
     * which is worse than no link.
     */
    issue_url:
      repositoryOf(row, state) && /^[0-9]+$/.test(String(row.ticket_id ?? ""))
        ? `https://github.com/${repositoryOf(row, state)}/issues/${row.ticket_id}`
        : "",
    plan: (state?.plan ?? null) as Record<string, unknown> | null,
    dev,
    review: (state?.review ?? null) as Record<string, unknown> | null,
    sre: (state?.sre ?? null) as Record<string, unknown> | null,
    // Lane G's agent and Lane H's record. Both are `None` on every run today --
    // neither is wired into a pipeline stage -- so these render as "not recorded"
    // rather than as an absence of findings. Reading a missing producer as a clean
    // result is this repository's signature defect.
    generated_tests: (state?.generated_tests ?? null) as Record<string, unknown> | null,
    retrieval: (state?.retrieval ?? null) as Record<string, unknown> | null,
    // **THE FIELD WHOSE ABSENCE BROKE THE WHOLE PAGE.** `runs/[runId]/page.tsx:192`
    // reads `run.awaiting_gates.length`, and an omitted key is `undefined`, so the
    // detail screen died in React with `Cannot read properties of undefined
    // (reading 'length')` -- rendered as "This page couldn't load". The three APIs
    // behind it all answered 200 with valid JSON, which is why nothing server-side
    // looked wrong.
    //
    // **THIS WAS `[]`, AND THAT MADE THE APPROVE BUTTON UNREACHABLE.** The reasoning
    // written here was that the field lists gates the QUEUE has paused, that an
    // Actions run never enters the queue, and that a gate held by a GitHub
    // Environment "is paused in GitHub, which this table cannot see". Every clause
    // was true, and the conclusion was still wrong: `page.tsx:192` renders
    // `GateControls` only `if (run.awaiting_gates.length > 0)`, so a hardcoded `[]`
    // means **no run ever shows a gate control** -- and the approval route, the
    // authorization, the dispatch to `pending_deployments` and the tests over all
    // three were reachable by nothing. A correct answer nobody asks for.
    //
    // The premise is also obsolete. The table CAN see it now: `run_index` writes the
    // state document onto the index row at every stage, and a gate is open exactly
    // when the stage before it is done and no `HumanDecision` names it -- which is
    // what `awaitingGates` reads, and what `run_facts` already decides approvals
    // over. Deriving the screen from one source and the decision from another is how
    // a button appears for a gate the server then refuses.
    //
    // **AND NOW IT COMES FROM GITHUB WHEN GITHUB CAN BE ASKED.** The premise above
    // — that the table can see a gate once `run_index` denormalises the state
    // document — is true and was not sufficient: the document learns of a decision
    // only when a CREDENTIALLED job rewrites it, so gate2 lagged by however long
    // `sre` took and gate3 never landed at all. `pending_deployments` is the only
    // authority on which Environment is held, and it is the same list `approveGate`
    // releases against — so the control and the refusal now read one source.
    awaiting_gates: progress
      ? gatesAwaiting(progress)
      : awaitingGates(state, String(state?.status ?? row.status ?? "")),
    /**
     * WHETHER THE ABOVE IS LIVE, so the screen never implies a freshness it does
     * not have. `false` means GitHub was not reachable (or the run has no Actions
     * page) and everything here is the stored record, which may be behind. An
     * interface that looked identical either way would be claiming currency it
     * could not deliver — the same reason the stream panel reports when it was
     * last heard from rather than showing a spinner.
     */
    live: progress !== null,
  };
}

export type ReaderRequest = {
  action?: string;
  tenant_id?: string;
  run_id?: string;
  full_names?: string[];
  by?: string;
};

/**
 * Dispatch, mirroring `web/lib/reader/runs.py:main` exactly -- including that a blank
 * tenant is REFUSED rather than translated to tenant zero. Translating it here would
 * let a caller with no session read the original single-tenant deployment's runs.
 */
export async function readTenancy(request: ReaderRequest): Promise<unknown> {
  const raw = request.tenant_id;
  if (typeof raw !== "string" || !raw.trim()) {
    throw new ReadRefused(
      "the reader was given no tenant, so the read has no scope",
      "a blank tenant is refused rather than translated to tenant zero",
    );
  }
  const tenantId = tenantForRunState(raw.trim());

  const needsRun = (): string => {
    const runId = request.run_id;
    // REFUSED BEFORE IT REACHES A KEY, and the value is never echoed back.
    if (typeof runId !== "string" || !isSafeRunId(runId)) throw new ReadRefused("no such run");
    return runId;
  };

  switch (request.action) {
    case "list_runs":
      return listRuns(tenantId);
    case "list_repositories":
      return listRepositories(tenantId);
    case "set_scope":
      return setScope(tenantId, request.full_names ?? [], request.by ?? "");
    case "run_facts":
      return runFacts(tenantId, needsRun());
    case "run_detail":
      return runDetail(tenantId, needsRun());
    case "run_cost":
      // The cost rows live on the state document. Reported as "nothing priced"
      // rather than as zero: `usd: null` means NOT PRICED and `0.0` means priced
      // and free, and collapsing them makes a missing table read as a free run.
      await requireRun(tenantId, needsRun());
      // EVERY FIELD `CostView` DECLARES. An earlier version returned a `total_usd`
      // the contract does not have and omitted `findings`, which `CostPanel.tsx:166`
      // reads as `findings.length` -- the same crash `awaiting_gates` caused, one
      // screen over.
      //
      // `usd: null` means NOT PRICED and `0.0` would mean priced and free; Lane E
      // measured that collapsing them makes a missing price table read as a free
      // run. `cache_hit_rate: null` is the same distinction -- a zero denominator
      // is not a zero rate.
      {
        // FROM THE DOCUMENT. `state.cost` is written by `merge_cost_records` in both
        // pipelines, so a run that reached the agents carries one row per stage.
        //
        // `usd: null` MEANS NOT PRICED and `0.0` means priced and free; Lane E
        // measured that collapsing them makes a missing price table read as a free
        // run. A run with no rows at all is the third case -- the usage recorder was
        // not wired on the path that ran it -- and `stages_priced: 0` is what says so.
        const cost = (stateOf(await requireRun(tenantId, needsRun())) ?? {}).cost as
          | Record<string, unknown>
          | undefined;
        const stages = Array.isArray(cost?.stages) ? cost.stages : [];
        return {
          run_id: request.run_id,
          usd: (cost?.usd as number | null) ?? null,
          stages_priced: stages.length,
          stages,
          cache_hit_rate: (cost?.cache_hit_rate as number | null) ?? null,
          findings: Array.isArray(cost?.findings) ? cost.findings : [],
        };
      }
    case "run_scoring":
      // `scan_provenance: ""` is the fourth field `ScoringResponse` declares, and
      // `""` is its documented "nobody recorded it" value -- rendered as unknown
      // rather than as a measured mode.
      {
        // `SecurityResult.scoring` is one row per finding, written by `score_findings`.
        const sec = (stateOf(await requireRun(tenantId, needsRun())) ?? {}).security as
          | Record<string, unknown>
          | undefined;
        const rows = Array.isArray(sec?.scoring) ? sec.scoring : [];
        return {
          run_id: request.run_id,
          // The threshold that produced an EMPTY table is still a fact worth
          // rendering -- otherwise a clean run and an unscanned one look identical.
          threshold: (rows[0] as Record<string, unknown> | undefined)?.threshold ?? null,
          rows,
          scan_provenance: (sec?.scan_provenance as string) ?? "",
        };
      }
    default:
      throw new ReadRefused(`unknown reader action ${JSON.stringify(request.action)}`);
  }
}

/**
 * The facts an approval is decided over. A DIFFERENT CONTRACT FROM `run_detail`,
 * and conflating them is how the approval button answered "no such run".
 *
 * Measured against the deployed app: `POST /api/approvals` returned
 * `404 {"error": "no such run. Nothing was recorded."}` for a run that was plainly
 * waiting at gate1. `runFacts` compares `payload.tenant_id !== tenantId` as defence
 * in depth, the detail shape carries no `tenant_id` at all, so the comparison was
 * `undefined !== "tenant-zero"` — it refused every approval, and refused it with the
 * message reserved for a cross-tenant attempt.
 *
 * **THAT IS THE RIGHT DIRECTION TO FAIL**, which is exactly why it was hard to see:
 * a missing fact refused an approval rather than permitting one.
 */
async function runFacts(tenantId: string, runId: string) {
  if (!indexTableName()) throw new ReadRefused("no such run");
  const row = await requireRun(tenantId, runId);
  const state = stateOf(row);
  const repositories = await rowsOfType(tenantId, SK_REPO);

  const status = String(state?.status ?? row.status ?? "");
  const known = ["running", "blocked", "rejected", "failed", "promoted"];

  return {
    run_id: String(row.run_id ?? runId),
    // FROM THE ROW, NOT THE ARGUMENT. `runFacts` re-checks this against the session's
    // tenant, and a value echoed from the caller would make that check compare a
    // string to itself.
    tenant_id: String(row.tenant_id ?? tenantId),
    // **THE TENANT'S SINGLE REPOSITORY, OR `""`.** `RunState` carries no repository
    // field -- `state.py` is frozen -- so the honest answer is the connected
    // repository when there is exactly one. `""` FAILS `authz.decide`'s scope check
    // and refuses; a guess would permit an approval against a repository nobody
    // named. The Python this replaces made the same choice and recorded it as a real
    // limit rather than a workaround.
    repository_full_name:
      repositories.length === 1 ? String(repositories[0]?.full_name ?? "") : "",
    status: known.includes(status) ? status : "failed",
    // WHICH GATE IS OPEN RIGHT NOW. Derived from the same evidence the stage spine
    // uses: the first gate the run has not yet decided, while the run is live. A
    // gate absent from this list may not be decided, whatever the reason.
    awaiting_gates: awaitingGates(state, status),
  };
}

/** The gate a live run is currently held at, as a list of at most one. */
function awaitingGates(state: RunStateDoc | null, status: string): string[] {
  if (!state) return [];
  if (["blocked", "rejected", "failed", "promoted"].includes(status)) return [];
  const decisions = Array.isArray(state.decisions)
    ? (state.decisions as Record<string, unknown>[])
    : [];
  const decided = new Set(decisions.map((d) => String(d.gate ?? "")));
  // The stage that must have finished before each gate can hold.
  const reached: [string, boolean][] = [
    ["gate1", state.plan != null],
    ["gate2", state.security != null],
    ["gate3", state.sre != null],
  ];
  for (const [gate, ready] of reached) {
    if (ready && !decided.has(gate)) return [gate];
    if (!ready) return [];
  }
  return [];
}

export type ApproveRequest = {
  run_id?: string;
  gate?: string;
  decision?: string;
  by?: string;
  reason?: string;
  tenant_id?: string;
};

/**
 * Record a gate decision, by releasing the GitHub Environment that holds the run.
 *
 * **OWNERSHIP FIRST, THROUGH THE TENANT-SCOPED CREDENTIAL**, exactly as the read path
 * does — and it matters more here, because the next thing this does is let a change
 * proceed toward `main`. A caller who does not own the run gets a refusal from a
 * credential that physically cannot see another tenant's index row, so there is no
 * branch in which a wrong-tenant approval reaches GitHub.
 *
 * `web/lib/authz.ts` has already refused ten other ways before this is called; this
 * is the last one, and it is the only one AWS enforces rather than application code.
 */
export async function approveRun(request: ApproveRequest): Promise<unknown> {
  const raw = request.tenant_id;
  if (typeof raw !== "string" || !raw.trim()) {
    throw new ReadRefused("the approval has no tenant, so it has no scope");
  }
  const tenantId = tenantForRunState(raw.trim());
  const runId = request.run_id;
  if (typeof runId !== "string" || !isSafeRunId(runId)) throw new ReadRefused("no such run");
  if (!indexTableName()) throw new ReadRefused("no run index is configured");

  const row = await requireRun(tenantId, runId);

  const ciRunId = typeof row.ci_run_id === "string" ? row.ci_run_id : "";
  if (!ciRunId) {
    // HONEST, AND NOT A CRASH. A run indexed before `ci_run_id` was recorded, or one
    // executed anywhere but GitHub Actions, genuinely cannot be approved from here
    // -- and saying "no such gate" would send somebody looking for a gate that is
    // waiting perfectly well.
    throw new ReadRefused(
      "this run cannot be approved from here",
      "it carries no GitHub run id, so there is no Environment to release. Approve " +
        "it in the Actions run itself.",
    );
  }

  const { approveGate } = await import("../dispatch");
  const decision = request.decision === "approved" ? "approved" : "rejected";
  const result = await approveGate(
    ciRunId,
    String(request.gate ?? ""),
    decision,
    String(request.by ?? ""),
    String(request.reason ?? ""),
  );

  // `status` is the RUN's status, which this call does not change -- GitHub releases
  // the job and the pipeline decides what happens next. Reporting "approved" as a run
  // status would claim an outcome nobody has reached yet.
  return { status: String(row.status ?? "running"), by: result.approvedAs };
}
