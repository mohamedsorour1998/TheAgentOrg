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
import { readPipeline } from "@/lib/pipeline";
import { currentIdentity } from "@/lib/session";

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
