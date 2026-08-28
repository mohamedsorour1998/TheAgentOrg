/**
 * GET /api/runs/[runId]/cost — what this run cost. Task I6, reading Lane E.
 *
 * TWO FIELDS WHOSE NULLS ARE LOAD-BEARING, and neither may be coerced on the way
 * out:
 *
 *   `usd: null`             not priced — an unknown model, or a table nobody updated
 *   `usd: 0.0`              priced, and it was free
 *   `cache_hit_rate: null`  a zero denominator; no model call to divide by
 *
 * A serialiser that turned either null into `0` would make a missing price table
 * look like a free run, which Lane E names as this project's signature defect shape.
 * `NextResponse.json` preserves both, and the contract types them `number | null`.
 *
 * `stages_priced` IS THE DISCRIMINATOR for "is cost instrumentation wired?", never
 * `usd`. An unwired run has zero rows and `usd: null`; a wired run has a row per
 * stage even when that stage spent nothing. `usd === 0` cannot tell them apart.
 *
 * THE CACHE HIT RATE IS CURRENTLY ZERO AND THAT IS MEASURED, NOT MISSING. Nothing in
 * `agentorg/` sets a Bedrock cache point, so all five agents pay full price for the
 * repository snapshot they re-send on every call — the largest silent cost in the
 * design. `findings` carries Lane E's own words for it, because "nobody reads a
 * percentage as an alarm".
 */

import { NextResponse } from "next/server";

import type { CostView } from "@/lib/endpoints";
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
      return refuse("sign in to see this run's cost", 401);
    }

    const { runId } = await context.params;

    try {
      const cost = await readPipeline<CostView>("detail", {
        action: "run_cost",
        tenant_id: session.tenantId,
        run_id: runId,
      });
      return respond(cost);
    } catch (error) {
      if (error instanceof PipelineError) {
        console.warn("[cost] read refused: %s", error.detail || error.message);
        return refuse("no such run", 404);
      }
      throw error;
    }
  } catch (error) {
    return unhandled(error);
  }
}
