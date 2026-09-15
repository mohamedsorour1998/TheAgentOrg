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

import { QueryCommand, PutCommand, GetCommand } from "@aws-sdk/lib-dynamodb";
import { randomUUID } from "node:crypto";

import { scopedClient } from "./credentials";
import { SK_REPO, SK_RUN, isSafeRunId, sk, tenantForRunState, tenantPk } from "./keys";

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

function summarise(row: Row) {
  return {
    run_id: String(row.run_id ?? ""),
    ticket_id: String(row.ticket_id ?? ""),
    status: (row.status as string) || "running",
    created_at: String(row.created_at ?? ""),
    // NULL, NOT A GUESS. These come from the run's STATE DOCUMENT, which lives in
    // `theagentorg-runs` and is only written when the pipeline runs on
    // STATE_BACKEND=dynamodb. Today it runs on the artifact handoff, so these are
    // genuinely unknown -- and `""` for provenance means "nobody recorded it",
    // which the UI renders as unknown rather than as a measured value.
    verdict: null,
    scan_provenance: "",
    blocking: null,
    awaiting_gate: "",
  };
}

async function listRuns(tenantId: string) {
  if (!indexTableName()) return { runs: [], indexed: false };
  const rows = await rowsOfType(tenantId, SK_RUN);
  rows.sort((a, b) => String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")));
  return { runs: rows.map(summarise), indexed: true };
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
 * Add repositories to this tenant's scope, then READ THE RESULT BACK.
 *
 * Echoing the request would reassure a caller that a refused write had landed. The
 * Python writer states the same rule, and it matters more here: this list is what
 * `authz.decide` consults before permitting an approval.
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

/**
 * Everything one run's detail screen needs that survives without the state document.
 *
 * OWNERSHIP FIRST, and it is the only check there is -- `requireRun` reads through the
 * TENANT-SCOPED credential, so a caller who does not own the run gets a refusal from a
 * credential that physically cannot see another tenant's index row.
 */
async function runDetail(tenantId: string, runId: string) {
  if (!indexTableName()) throw new ReadRefused("no run index is configured");
  const row = await requireRun(tenantId, runId);
  return {
    ...summarise(row),
    ticket_text: "",
    model_provenance: "",
    trigger: "",
    poisoned: false,
    pr_url: null,
    branch: null,
    stages: [],
    decisions: [],
    // NULL RATHER THAN AN EMPTY OBJECT. An empty security panel and an unscanned run
    // must not render identically; `null` is what the UI reads as "not available".
    security: null,
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
    case "run_detail":
      return runDetail(tenantId, needsRun());
    case "run_cost":
      // The cost rows live on the state document. Reported as "nothing priced"
      // rather than as zero: `usd: null` means NOT PRICED and `0.0` means priced
      // and free, and collapsing them makes a missing table read as a free run.
      await requireRun(tenantId, needsRun());
      return { run_id: request.run_id, stages: [], total_usd: null, stages_priced: 0 };
    case "run_scoring":
      await requireRun(tenantId, needsRun());
      return { run_id: request.run_id, rows: [], threshold: null };
    default:
      throw new ReadRefused(`unknown reader action ${JSON.stringify(request.action)}`);
  }
}
