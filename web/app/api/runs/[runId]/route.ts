/**
 * GET /api/runs/[runId] — one run: stages, decisions, verdict, open gates. Task I3.
 *
 * TENANT-SCOPED, AND A CROSS-TENANT READ ANSWERS 404. `accessors.get_run` raises
 * `CrossTenantAccess` for another tenant's run and `NotFound` for an absent one; the
 * reader reports both as "no such run" and this route turns that into 404 for both.
 * Distinguishing them would itself be the disclosure — a run id is an unguessable
 * uuid, so 403 would confirm somebody else's run exists.
 *
 * THE RUN ID IS NOT VALIDATED HERE. `log.is_safe_run_id` is applied in the reader,
 * where the value becomes a path — the same guard `queue.enqueue` and
 * `adopt_run_id` apply, for the reason `approve_server` records: `gates._state_path`
 * would happily resolve `../../etc/passwd` outside `runs/`. A second copy here would
 * be the weaker duplicate `web/lib/pipeline.ts` argues against.
 */

import { NextResponse } from "next/server";

import type { RunDetail } from "@/lib/contract";
import { refuse, respond, unhandled } from "@/lib/http";
import { PipelineError, readPipeline } from "@/lib/pipeline";
import { currentIdentity } from "@/lib/session";

export async function GET(
  _request: Request,
  context: { params: Promise<{ runId: string }> },
): Promise<NextResponse> {
  try {
    const session = await currentIdentity();
    if (session === null) {
      return refuse("sign in to see this run", 401);
    }

    const { runId } = await context.params;

    try {
      const detail = await readPipeline<RunDetail>("detail", {
        action: "run_detail",
        tenant_id: session.tenantId,
        run_id: runId,
      });
      return respond(detail);
    } catch (error) {
      if (error instanceof PipelineError) {
        // EVERY REFUSED READ IS 404 HERE, including one that failed for an unrelated
        // reason. That is deliberate at this route: the reader's refusals for this
        // action are all "no such run", so mapping the class to 404 keeps the
        // cross-tenant and absent cases identical. A genuine fault would otherwise
        // need its own status, and choosing one per failure mode is how the two
        // become distinguishable again.
        //
        // The detail is logged, not sent: it may name a filesystem path.
        console.warn("[runs] read refused: %s", error.detail || error.message);
        return refuse("no such run", 404);
      }
      throw error;
    }
  } catch (error) {
    return unhandled(error);
  }
}
