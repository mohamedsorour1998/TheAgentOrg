/**
 * GET /api/runs — this tenant's runs, newest first. Task I3.
 *
 * TENANT-SCOPED THROUGH LANE B, and the scoping is not in this file. The tenant
 * comes from `currentIdentity()` (a verified session, never a query parameter), and
 * the read goes through `accessors.list_runs(scope)` inside the Python reader —
 * whose `WHERE tenant_id = ?` is the predicate whose removal fails 13 named tests.
 *
 * There is deliberately no `?tenant=` parameter and no way to ask for another
 * tenant's runs. A tenant a caller can name is a tenant a caller can choose.
 */

import { NextResponse } from "next/server";

import type { RunListResponse } from "@/lib/endpoints";
import { refuse, respond, unhandled } from "@/lib/http";
import { DispatchRefused, startRun } from "@/lib/dispatch";
import { readPipeline } from "@/lib/pipeline";
import { currentIdentity } from "@/lib/session";

/**
 * POST /api/runs — start a pipeline run.
 *
 * **POST ONLY, AND IT SPENDS MONEY.** A run invokes five Bedrock runtimes and opens
 * a pull request on somebody's repository, so a GET here would be reachable by a
 * prefetch or a back button. Same rule as `POST /api/approvals`.
 *
 * **THE TENANT MUST HAVE THE REPOSITORY IN SCOPE, AND THAT IS THE REAL REFUSAL.**
 * The dispatch token can start any workflow on this repository; what bounds this
 * route is that it reads the caller's own scope through the TENANT-SCOPED credential
 * first, and refuses when it is empty. An empty scope is a refusal and never an
 * exemption — the rule `authz.decide` already applies to approvals, and the one Lane
 * K applies to an empty key store.
 *
 * **IT RETURNS NO RUN ID, because there is not one yet.** `workflow_dispatch` answers
 * 204 with no body; the id is minted by the `plan` job and reaches this application
 * only when `run_index.record_run` writes it. Inventing one, or echoing the ticket as
 * if it were an id, would be a value the caller could send back.
 */
export async function POST(request: Request): Promise<NextResponse> {
  try {
    const session = await currentIdentity();
    if (session === null) {
      return refuse("sign in to start a run", 401);
    }

    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return refuse("the request could not be parsed", 400);
    }
    const { title, body: detail, poisoned } = (body ?? {}) as Record<string, unknown>;
    if (typeof title !== "string") {
      return refuse("say what the change should do", 400);
    }

    // SCOPE FIRST, before the token is even read. A caller with nothing in scope
    // must not be able to make this route fetch a credential.
    const scope = await readPipeline<{ repositories: { full_name: string }[] }>(
      "repositories",
      { action: "list_repositories", tenant_id: session.tenantId },
    );
    if (scope.repositories.length === 0) {
      return refuse(
        "add a repository to this tenant's scope before starting a run",
        409,
      );
    }

    try {
      const started = await startRun({
        title,
        body: typeof detail === "string" ? detail : "",
        // COERCED HERE, not trusted. A body may send anything; `poisoned` decides
        // whether the developer agent puts a fake credential in the diff, so it
        // must be exactly true and never a truthy string.
        poisoned: poisoned === true,
      });
      // THE ISSUE NUMBER IS RETURNED because this route CREATED it -- it is a real
      // thing the caller can open, unlike the run id, which does not exist until
      // the plan job mints it.
      return NextResponse.json({ ok: true, issue: started.issue, next: "poll" }, { status: 202 });
    } catch (error) {
      if (error instanceof DispatchRefused) {
        return NextResponse.json(
          { error: error.message, detail: error.detail },
          { status: 400 },
        );
      }
      throw error;
    }

    // Unreachable: the try block above returns on success. Kept so the function
    // has one exit type rather than relying on TypeScript's inference of a throw.
    return NextResponse.json({ ok: true, next: "poll" }, { status: 202 });
  } catch (error) {
    return unhandled(error);
  }
}

export async function GET(): Promise<NextResponse> {
  try {
    const session = await currentIdentity();
    if (session === null) {
      return refuse("sign in to see your runs", 401);
    }

    // `indexed` travels with the list. An empty `runs` array means two different
    // things — nothing indexes runs in this deployment (`TENANT_DB` unset, so
    // `run_index.record_run` is a no-op by design) versus this tenant has had none —
    // and a screen must say which. See `web/lib/reader/runs.py`.
    const answer = await readPipeline<RunListResponse>("runs", {
      action: "list_runs",
      tenant_id: session.tenantId,
    });

    return respond(answer);
  } catch (error) {
    return unhandled(error);
  }
}
