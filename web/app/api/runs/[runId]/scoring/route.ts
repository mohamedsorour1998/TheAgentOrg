/**
 * GET /api/runs/[runId]/scoring — Lane C's transparency table. Task I7.
 *
 * THE JUDGES' QUESTION, ANSWERED AS DATA. Asked at the pre-final: "gitleaks and
 * trivy — how do we score the response so we know it is go or no-go, as you claimed
 * it is deterministic". The verdict was already deterministic; what was missing was
 * the ability to SHOW the arithmetic. This endpoint is that showing.
 *
 * `native` IS THE FIELD THAT MAKES A ROW WORTH HAVING, and Lane J must render it
 * beside `mapped` rather than instead of it. The three scanners do not agree on a
 * vocabulary — trivy emits UNKNOWN/LOW/MEDIUM/HIGH/CRITICAL, semgrep emits both
 * INFO/WARNING/ERROR and LOW/MEDIUM/HIGH/CRITICAL depending on rule vintage, and
 * **gitleaks emits no severity at all**. Printing only our mapped value would hide
 * that difference; printing both makes the mapping auditable by a reader who has the
 * scanner's own output in front of them.
 *
 * So `native: ""` is a FACT ABOUT GITLEAKS, not a gap: it reports RuleID, File,
 * StartLine, Description and an entropy score, nothing that ranks the hit. Its
 * severity is a POLICY — any finding from a secret scanner is `critical`, because a
 * committed credential has no lesser grade — and Lane C distinguishes `""`
 * (the scanner has nothing to say) from `<not recorded>` (it said something and this
 * row lost it). Rendering them the same would make a gap read as data about gitleaks.
 *
 * AN EMPTY `rows` IS TWO DIFFERENT FACTS, which is why `threshold` and
 * `scan_provenance` travel at the top level: a run whose scanners found nothing has
 * an empty list, and so does a run written before `SecurityResult.scoring` existed.
 * `scan_provenance` is what distinguishes them — `""` means nobody recorded it.
 *
 * KNOWN GAP, STATED: Lane C's note says `SecurityResult.scoring` is populated by
 * `score_findings` but its two call sites belong to other lanes, so **no deployed run
 * carries a scoring row yet**. This endpoint reads the field correctly and will be
 * empty until `agents/security.py` emits them. That is a wiring gap in another lane's
 * file, not something this route can close, and an empty table here is the honest
 * answer rather than a fabricated one.
 */

import { NextResponse } from "next/server";

import type { ScoringResponse } from "@/lib/endpoints";
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
      return refuse("sign in to see this run's scoring", 401);
    }

    const { runId } = await context.params;

    try {
      const scoring = await readPipeline<ScoringResponse>("detail", {
        action: "run_scoring",
        tenant_id: session.tenantId,
        run_id: runId,
      });
      return respond(scoring);
    } catch (error) {
      if (error instanceof PipelineError) {
        console.warn("[scoring] read refused: %s", error.detail || error.message);
        return refuse("no such run", 404);
      }
      throw error;
    }
  } catch (error) {
    return unhandled(error);
  }
}
