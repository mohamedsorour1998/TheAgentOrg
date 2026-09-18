/**
 * HOW THE WEB LAYER REACHES THE PIPELINE. Read this before adding an endpoint.
 *
 * =========================================================================
 * THE DECISION: this layer calls the PYTHON MODULES as a subprocess, and does
 * NOT call Lane K's HTTP API. Three measured reasons, not a preference.
 * =========================================================================
 *
 * 1. LANE K HAS NO APPROVAL ROUTE, BY DESIGN. Measured — its eight routes are
 *    health, submit, status, cancel, config read/write, ingress and openapi, and
 *    its scopes are `runs:{read,write}` and `config:{read,write}`. There is no
 *    `gates:approve`, no route maps to `gates.resume` or `queue.resume`, and an
 *    AST test enforces that per module. So I5 — the task this lane exists for —
 *    cannot be served by it at all, whatever else it could serve.
 *
 * 2. ITS KEY STORE IS IN-PROCESS. `auth.InMemoryKeyStore` is module state, so
 *    `issue_key` writes into the store of whichever process called it. A Node
 *    process cannot provision a key into a separate Python server, which means
 *    calling that API over HTTP requires an out-of-band provisioning step that
 *    does not survive either process restarting. Its own docstring names this as
 *    one of three known gaps.
 *
 * 3. THE TENANT COMES FROM ITS CREDENTIAL, NOT FROM A SESSION. Every Lane K route
 *    derives the tenant from the machine key, and `test_no_route_takes_a_tenant_
 *    from_the_request` asserts structurally that none reads one from a request. A
 *    web session's tenant therefore has nowhere to go: it would need one key per
 *    tenant, minted per process, which is a worse version of the session we
 *    already have.
 *
 * WHAT THIS DOES INSTEAD, AND THE PROPERTY THAT MATTERS
 * ====================================================
 * One short-lived `python` subprocess per read, running a named module in
 * `agentorg/`. So Lane B's tenant scoping, Lane A's queue, Lane C's scoring and
 * Lane E's cost are reached through THEIR OWN accessors, in Python, with
 * `engine.acting_as(tenant)` bound around every read.
 *
 * **THIS API MUST NOT BECOME A WAY AROUND LANE B'S ENFORCEMENT**, which is the
 * single worst thing this lane could ship. A Node-side database client would be
 * exactly that: `node:sqlite` is available (measured — `DatabaseSync` is in the
 * stdlib here), so reimplementing `WHERE tenant_id = ?` in TypeScript is
 * genuinely possible, and it would be a SECOND, WEAKER copy of the predicate
 * whose removal fails 13 named Python tests. It would also miss the SQLite
 * triggers entirely, since those compare against `current_tenant()` — an
 * application-defined function registered only by `db.engine.connect()`. A
 * connection opened from Node has no such function, so every scoped write fails
 * with "no such function", and every scoped READ silently succeeds unscoped.
 *
 * That asymmetry is the whole argument. Lane B's own ADR says it: SQLite cannot
 * constrain a SELECT, so on the tested path a read is only as scoped as its
 * accessor. Reaching the data any way other than through those accessors means
 * re-deriving the one predicate that does the work.
 *
 * ============================================================================
 * EVERYTHING ABOVE WAS MEASURED, CORRECT, AND IS NO LONGER THE SHIPPED DESIGN.
 * Kept in full, because the reasoning is what makes the change safe to judge.
 * ============================================================================
 *
 * TWO THINGS CHANGED, ONE OF THEM FATAL TO THE SUBPROCESS.
 *
 * 1. **THE SUBPROCESS CANNOT RUN WHERE THIS CODE NOW RUNS.** Measured 2026-09-15
 *    against the deployed app, signed in as a real reviewer whose session carried
 *    `tenant_id: tenant-zero`:
 *
 *        /api/runs         -> PipelineError: the pipeline reader could not be started
 *        /api/repositories -> PipelineError: the pipeline reader could not be started
 *
 *    An Amplify SSR Lambda has no Python, no virtualenv and no repository checkout.
 *    Every Python reader was correct, tested, tenant-scoped and UNABLE TO START.
 *
 * 2. **THE ARGUMENT AGAINST A NODE CLIENT WAS ABOUT SQL, AND THE DATABASE IS NO
 *    LONGER SQL.** Every objection above turns on `WHERE tenant_id = ?` and on
 *    `current_tenant()` — an application-defined SQLite function. Under DynamoDB
 *    the scoping is `dynamodb:LeadingKeys` compared against an IAM SESSION TAG, so
 *    there is no predicate for a client to re-derive weakly. `lib/dynamo/` assumes
 *    the same role with the same tag; AWS applies the same condition. A wrong
 *    partition key returns AccessDenied from AWS, not rows.
 *
 * So the enforcement did not move into TypeScript. It moved OUT of application code
 * entirely, into the credential — which is the whole point of the DynamoDB
 * migration, and the reason a Node reader went from "the worst option" to the only
 * one that runs.
 *
 * `readPipelineViaSubprocess` is retained below, unreachable from the routes, for the
 * self-hosted stack — where a Python interpreter and the repository both exist, and
 * where it remains the executable proof that `agentorg/tenancy/accessors.py` is the
 * one tenant-scoping layer.
 *
 * THE COST, MEASURED AND STATED
 * =============================
 * A subprocess per read is slower than an in-process query. Measured on this
 * machine: importing `agentorg.tenancy.accessors`, `agentorg.queue`,
 * `agentorg.gates` and `agentorg.log` together takes **0.183s total** wall clock
 * (`0.08s user 0.03s system`). So the floor is roughly 200ms per read, which is
 * fine for a run list and a detail screen and is NOT fine for a two-second poll —
 * which is one more reason I4 is a stream rather than a poll.
 *
 * The alternative that would remove it is a long-lived Python process speaking
 * HTTP, i.e. Lane K's server with an approval route and session-derived tenancy.
 * That is the right end state and it is Lane K's file, not mine. Stated as a
 * known limit rather than worked around.
 */

import { spawn } from "node:child_process";
import path from "node:path";

/**
 * The repository root, from this file's location. `web/lib/pipeline.ts` → two up.
 *
 * Derived rather than configured, for `fixtures_loader`'s reason: it resolves
 * `fixtures/` from the repo root because a configured path is one more thing that
 * can be wrong in a deployment nobody is watching.
 */
export const REPO_ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "..",
);

/**
 * The interpreter. `.venv-main/bin/python` unless overridden.
 *
 * CLAUDE.md is explicit that `.venv-habiba` / `.venv-sorour` / `.venv-testing`
 * each carry an editable-install `.pth` pointing at a sibling worktree, so imports
 * resolve somewhere other than where you are editing. Naming the interpreter
 * rather than trusting `PATH` is what keeps this from picking one of those up.
 */
export const PYTHON =
  process.env.AGENTORG_PYTHON ?? path.join(REPO_ROOT, ".venv-main", "bin", "python");

/**
 * Where the reader scripts live. Under `web/lib/`, which this lane owns.
 *
 * `import.meta.url` rather than `__dirname`: this package is `"type": "module"`,
 * so `__dirname` is not defined and the CommonJS form fails at runtime rather
 * than at build time.
 */
export const READERS = path.join(
  path.dirname(new URL(import.meta.url).pathname),
  "reader",
);

/**
 * The reader scripts, named ONE BY ONE rather than built from a caller's string.
 *
 * =========================================================================
 * A MEASURED BUILD DEFECT, not defensiveness. The first version spawned
 * `path.join(READERS, `${moduleName}.py`)`, and `next build` reported:
 *
 *     Static analysis determined that this filesystem access causes the whole
 *     project to be traced and included in the output. This is usually
 *     unintentional and leads to all source files (including the public folder)
 *     to be deployed as part of the server code.
 * =========================================================================
 *
 * "The whole project" here is a repository whose `runs/` directory holds ~10k
 * gitignored files that CLAUDE.md says never to list. Turbopack could not prove which
 * file the template reached, so it conservatively traced everything into the server
 * bundle — a deployment that succeeds, ships the entire repository, and slows or fails
 * on a size limit. Exactly the shape this project is careful about: a build reporting
 * success while doing something nobody asked for.
 *
 * A LITERAL MAP FIXES IT AND IS BETTER ANYWAY. `readPipeline` now takes a KEY, not a
 * path fragment, so a caller cannot name a file — the union type makes a typo a
 * compile error rather than a subprocess that fails at runtime, and no request value
 * can influence which script runs. That was already true (every call site passes a
 * literal) and is now enforced by the type system rather than by inspection.
 */
const READER_SCRIPTS = {
  runs: "runs.py",
  detail: "detail.py",
  approve: "approve.py",
  repositories: "repositories.py",
} as const;

export type ReaderName = keyof typeof READER_SCRIPTS;

/** How long one read may take before it is abandoned, in milliseconds. */
const TIMEOUT_MS = 20_000;

/** The largest answer a read may produce. A run's detail is kilobytes, not megabytes. */
const MAX_OUTPUT_BYTES = 8 * 1024 * 1024;

export class PipelineError extends Error {
  constructor(
    message: string,
    readonly detail: string = "",
    /**
     * A DELIBERATE REFUSAL, not a fault — so it is a 4xx and not a 500.
     *
     * **REPORTED FROM THE DEPLOYED APP.** Approving a run that carries no GitHub
     * run id answered `HTTP 500 — PipelineError: this run cannot be approved from
     * here`, under the words *"Retry once. If it happens again this needs an
     * operator, not a refresh."* Every part of that is wrong: nothing failed, the
     * answer is deterministic, and retrying can only produce it again.
     *
     * The comment at the `ReadRefused` branch below claimed `lib/http.ts` "keeps
     * mapping it to the same status" — it did not; `unhandled` maps everything to
     * 500. A comment asserting a mapping that does not exist is how the wrong
     * status survived being read.
     */
    readonly refused: boolean = false,
  ) {
    super(message);
    this.name = "PipelineError";
  }
}

/**
 * Run one reader script with a JSON request on stdin, and parse its answer.
 *
 * THE READERS LIVE UNDER `web/lib/reader/`, invoked BY PATH rather than as `-m`.
 * They are Python, and they are this lane's files: the alternative was a new
 * top-level `agentorg_web/` package, which would put Lane I's code in the
 * repository root beside `agentorg/` and outside the paths this lane owns. A
 * reader that reads is not part of the pipeline package.
 *
 * ARGUMENTS GO OVER STDIN AS JSON, NEVER ON THE COMMAND LINE. A run id and a
 * tenant id both reach this function from a request, and `argv` interpolation is
 * where a shell injection lives. `spawn` with an argument ARRAY does not invoke a
 * shell at all, and putting the untrusted values in the BODY rather than in argv
 * means even a future change to a shell-invoking spawn cannot reach them.
 *
 * THE VALUES ARE STILL VALIDATED ON THE PYTHON SIDE. `log.is_safe_run_id` refuses a
 * traversal, and `queue.enqueue` / `adopt_run_id` already validate for the same
 * reason: a run id becomes a path component, a partition key AND a subprocess
 * argument. This function does not attempt to be that check — a validation
 * reimplemented here would be the second weaker copy this module's header argues
 * against.
 *
 * PYTHONPATH IS SET TO THE REPO ROOT, and this is a measured requirement rather
 * than a precaution. CLAUDE.md records `cf5cb83`: a subprocess run from `scripts/`
 * has `sys.path[0]` pointing there, so the worktree root never reaches `sys.path`
 * and the editable install resolves `agentorg` to the SHARED CHECKOUT — a stage
 * wrote one checkout's `runs/` while a test globbed another's. Three lanes each
 * lost time to it.
 */
async function readPipeline<T>(
  moduleName: ReaderName,
  request: Record<string, unknown>,
): Promise<T> {
  // ── THE READS RUN IN-PROCESS NOW, AND THE SUBPROCESS BELOW IS UNREACHABLE ────
  //
  // MEASURED 2026-09-15 against the deployed app, signed in as a real reviewer
  // whose session carried `tenant_id: tenant-zero`:
  //
  //     /api/runs         -> PipelineError: the pipeline reader could not be started
  //     /api/repositories -> PipelineError: the pipeline reader could not be started
  //
  // The `spawn` below launches `.venv-main/bin/python`, and an Amplify SSR Lambda
  // has no Python, no virtualenv and no repository checkout. So every Python reader
  // was correct, tested, tenant-scoped and UNABLE TO START. Sign-in worked and every
  // screen behind it was an error -- this repository's signature pattern at the
  // largest scale it has appeared.
  //
  // `lib/dynamo/` is the replacement: the same queries, against the same table, on a
  // credential minted by the SAME AssumeRole-with-a-session-tag the Python used. The
  // scoping did not move into TypeScript -- it lives in the credential, and AWS
  // enforces it either way.
  //
  // THE REFUSAL CONTRACT IS UNCHANGED, which is why no route needed editing: a
  // `ReadRefused` becomes the same `PipelineError` the `{error, detail}` envelope
  // produced, so `lib/http.ts` keeps mapping it to the same status.
  const { readTenancy, ReadRefused, approveRun } = await import("./dynamo/reader");
  try {
    // THE APPROVAL IS A WRITE TO GITHUB, NOT A READ FROM DYNAMODB, and it is routed
    // by MODULE NAME because that is what `recordDecision` already passes. The
    // Python it replaced sent no `action` key at all, so dispatching on the request
    // body would have sent it to the "unknown action" branch -- which is exactly
    // what happened after the port: every approval answered an error for a gate the
    // application could see was waiting.
    if (moduleName === "approve") {
      return (await approveRun(request as Parameters<typeof approveRun>[0])) as T;
    }
    return (await readTenancy(request as Parameters<typeof readTenancy>[0])) as T;
  } catch (error) {
    if (error instanceof ReadRefused) {
      // `refused: true` -- a decision, not a failure. `lib/http.ts:unhandled` turns
      // it into a 409, so the screen stops telling somebody to retry an answer that
      // cannot change.
      throw new PipelineError(error.message, error.detail, true);
    }
    // A CREDENTIAL FAILURE IS NOT "no such run". `TenantCredentialError` means the
    // deployment is misconfigured -- an unset role ARN, or a session tag STS
    // refused -- and reporting it as an empty list would hide a broken deployment
    // behind a screen that reads as "you have no runs".
    // **THE INNER `detail` IS CARRIED, AND DROPPING IT COST A DIAGNOSIS.** This
    // used to report `${name}: ${message}` only. An approval failed with
    // `DispatchRefused: the decision was not recorded` -- true, and useless: the
    // sentence naming the actual cause was sitting in that error's OWN `detail`
    // field (`GitHub answered 403: Resource not accessible by personal access
    // token`), which this line discarded. The failure had to be reproduced by hand
    // against the live API to learn a thing the server already knew.
    //
    // Errors in this codebase carry a two-part shape on purpose: a `message` a
    // person may be shown and a `detail` for the log. A wrapper that keeps only
    // the first turns every careful refusal into "something went wrong".
    const inner = error as Error & { detail?: string };
    const detail = typeof inner.detail === "string" && inner.detail ? ` -- ${inner.detail}` : "";
    throw new PipelineError(
      `the ${moduleName} read failed`,
      `${inner.name}: ${inner.message}${detail}`,
    );
  }
}

/**
 * THE PYTHON SUBPROCESS PATH. Retained, unreachable, and deliberately not deleted.
 *
 * It is how `infra/selfhost/docker-compose.yml` runs the same reads, where a Python
 * interpreter and the repository both exist -- and it carries the only executable
 * proof that `agentorg/tenancy/accessors.py` remains the one tenant-scoping layer.
 * Deleting it would also delete the argument for keeping the two in agreement.
 */
async function readPipelineViaSubprocess<T>(
  moduleName: ReaderName,
  request: Record<string, unknown>,
): Promise<T> {
  const body = JSON.stringify(request);

  return new Promise<T>((resolve, reject) => {
    // ── `turbopackIgnore` — MEASURED, AND THE ALTERNATIVE IS WORSE ─────────────
    //
    // `next build` warns here, and the warning is correct about what it sees:
    //
    //     Static analysis determined that this filesystem access causes the whole
    //     project to be traced and included in the output. This is usually
    //     unintentional and leads to all source files (including the public folder)
    //     to be deployed as part of the server code.
    //
    // "The whole project" is a repository whose `runs/` holds ~10k gitignored files
    // CLAUDE.md says never to list, so tracing it into the server bundle is a
    // deployment that succeeds and ships the entire repository. The paths are
    // genuinely runtime values -- `REPO_ROOT` and `READERS` derive from
    // `import.meta.url`, and `PYTHON` from the environment -- because the thing being
    // launched is an EXTERNAL INTERPRETER, not a module Turbopack could bundle.
    //
    // Narrowing the filename to a literal map (above) did not silence it, measured:
    // the warning is about the whole `spawn` call, not the argument. So the honest
    // options were the documented opt-out or pretending the paths are static.
    //
    // WHAT THIS COSTS, STATED: the reader scripts and the Python interpreter are NOT
    // bundled and must exist on the deployment host. That is already true -- this
    // layer shells out to `.venv-main/bin/python` and reads `agentorg/`, neither of
    // which a JavaScript bundler can carry -- so the ignore records a fact rather than
    // creating one. `docker compose` mounts the repository, which is why the
    // self-hosted stack is the deployment this is written for.
    const child = spawn(/* turbopackIgnore: true */ PYTHON, [path.join(READERS, READER_SCRIPTS[moduleName])], {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PYTHONPATH: REPO_ROOT,
        // The web layer reads; it never runs a stage. Closing the GitHub seam
        // means a read cannot post a comment or open a pull request even if a
        // future module reached for one by mistake.
        OFFLINE: "true",
        LLM_DISABLED: "true",
      },
      // No shell. The argument array is passed to execve directly.
      shell: false,
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let size = 0;
    let settled = false;

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        child.kill("SIGKILL");
        reject(
          new PipelineError(
            "the pipeline read did not finish in time",
            `${moduleName} exceeded ${TIMEOUT_MS}ms`,
          ),
        );
      }
    }, TIMEOUT_MS);

    child.stdout.on("data", (chunk: Buffer) => {
      size += chunk.length;
      // BOUNDED BEFORE IT IS ACCUMULATED, the way `agents/server.py` checks its
      // 4 MiB cap BEFORE the read "so a hostile length cannot make the container
      // allocate". Here the producer is our own code, so this is a guard against
      // a runaway loop rather than against an attacker — but the allocation is
      // just as real.
      if (size > MAX_OUTPUT_BYTES) {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          child.kill("SIGKILL");
          reject(
            new PipelineError(
              "the pipeline read produced more output than this layer will hold",
              `${moduleName} exceeded ${MAX_OUTPUT_BYTES} bytes`,
            ),
          );
        }
        return;
      }
      stdout += chunk.toString("utf8");
    });

    child.stderr.on("data", (chunk: Buffer) => {
      // Kept, and surfaced on failure only. A Python warning on a successful read
      // is not an error, and treating it as one would make the layer fail on
      // deprecation notices.
      stderr += chunk.toString("utf8");
    });

    child.on("error", (error: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(
        new PipelineError(
          "the pipeline reader could not be started",
          `${PYTHON}: ${error.message}`,
        ),
      );
    });

    child.on("close", (code: number | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);

      if (code !== 0) {
        reject(
          new PipelineError(
            "the pipeline read failed",
            // The exit code AND the stderr, because "the read failed" without
            // either is the reassuring non-answer this repository keeps paying
            // for. Truncated: a traceback is long and this reaches a log.
            `${moduleName} exited ${code}: ${stderr.slice(-2000)}`,
          ),
        );
        return;
      }

      // AN EMPTY BODY IS NOT PARSED AS `{}`. `agent_client` refuses a zero-byte
      // body for exactly this reason: it "makes a blank response
      // indistinguishable from a runtime that answered `{}`". A reader that
      // printed nothing did not answer.
      if (stdout.trim() === "") {
        reject(
          new PipelineError(
            "the pipeline read returned nothing",
            `${moduleName} exited 0 and printed no JSON`,
          ),
        );
        return;
      }

      let parsed: unknown;
      try {
        parsed = JSON.parse(stdout);
      } catch (error) {
        reject(
          new PipelineError(
            "the pipeline read could not be parsed",
            `${moduleName}: ${(error as Error).message}`,
          ),
        );
        return;
      }

      if (parsed === null || typeof parsed !== "object") {
        reject(
          new PipelineError(
            "the pipeline read answered something that is not an object",
            `${moduleName} answered ${typeof parsed}`,
          ),
        );
        return;
      }

      // AN `error` KEY IS A REFUSAL, not a result. The Python side reports a
      // refused read (a cross-tenant access, an absent run) as data rather than
      // as a non-zero exit, because those are answers and not crashes — but they
      // must not be handed back as though they were the thing asked for.
      const record = parsed as Record<string, unknown>;
      if (typeof record.error === "string") {
        reject(
          new PipelineError(
            record.error,
            typeof record.detail === "string" ? record.detail : "",
          ),
        );
        return;
      }

      resolve(record as T);
    });

    child.stdin.on("error", () => {
      // A closed stdin is reported by the `close` handler above with the exit
      // code; swallowing it here only stops an unhandled EPIPE from crashing the
      // Node process. Deliberately not a reject: doing both races the two.
    });
    child.stdin.end(body);
  });
}

// `readPipelineViaSubprocess` is EXPORTED so it is not dead code to the linter,
// and because naming it in the public surface is what records that it is kept on
// purpose -- the self-hosted stack's path, and the executable proof that
// `agentorg/tenancy/accessors.py` is still the one tenant-scoping layer.
export { readPipeline, readPipelineViaSubprocess };
