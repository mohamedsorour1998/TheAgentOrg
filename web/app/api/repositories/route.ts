/**
 * GET / PUT /api/repositories — which repositories are in scope. Task I2.
 *
 * WHY THIS MATTERS BEYOND CONVENIENCE: this list is what `authz.decide` checks a gate
 * approval against. A repository in scope is one whose runs this tenant may approve,
 * so **editing this list is editing an authorisation boundary** — which is why PUT
 * carries the same `Origin` check as the approvals route rather than only the read
 * being protected.
 *
 * NO SECOND TABLE. The scope lives in Lane B's `repository`, which already carries the
 * isolation triggers, the Postgres RLS policy and `UNIQUE (tenant_id, full_name)` so
 * two customers may both connect `acme/auth-service`. A `web_repository_scope` table
 * here would be a second answer to the question an approval reads, and two tables that
 * can disagree means an approval permitted by one and refused by the other with
 * nothing recording which was consulted.
 *
 * WHAT "IN SCOPE" DOES NOT MEAN. It does not narrow the GitHub token — OAuth's `repo`
 * scope is all-or-nothing across every repository the person can reach, and there is
 * no per-repository OAuth scope. So this list is enforced by THIS application, not by
 * GitHub, and a compromised token is not limited by it. The honest fix is a GitHub App
 * installation (per-repository grants, installation tokens, a different authorisation
 * model), recorded in `web/lib/auth.ts` as a further step rather than half-built.
 */

import { NextResponse } from "next/server";

import type { RepositoryListResponse } from "@/lib/endpoints";
import { originIsAcceptable } from "@/lib/authz";
import { readJson, refuse, respond, unhandled } from "@/lib/http";
import { allowedOrigins } from "@/lib/origins";
import { readPipeline } from "@/lib/pipeline";
import { currentIdentity } from "@/lib/session";

/** `owner/name`, and nothing else. */
const FULL_NAME = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/;

/**
 * Is this a repository full name?
 *
 * ANCHORED AT BOTH ENDS, and that is load-bearing for `github_ops._ISSUE_REF`'s
 * reason: without the anchors `acme/auth extra`, `acme/auth\nother` and
 * `../../etc/passwd` all match a substring somewhere. This value is compared against
 * `RunFacts.repositoryFullName` to authorise an approval, and it reaches a URL.
 *
 * `[A-Za-z0-9._-]` rather than `\w`, deliberately, and the reason is the one CLAUDE.md
 * records about `\d`: JavaScript's `\w` is ASCII-only so it is safe here, but `\w`
 * ALSO admits `_` while excluding `.` and `-`, which are legal in GitHub names — so
 * spelling the class out is both narrower and more correct. Exactly one `/`.
 */
function isFullName(value: unknown): value is string {
  return typeof value === "string" && FULL_NAME.test(value);
}

export async function GET(): Promise<NextResponse> {
  try {
    const session = await currentIdentity();
    if (session === null) {
      return refuse("sign in to see your repositories", 401);
    }

    const answer = await readPipeline<{
      repositories: { full_name: string }[];
      indexed: boolean;
    }>("runs", { action: "list_repositories", tenant_id: session.tenantId });

    const body: RepositoryListResponse = {
      repositories: answer.repositories.map((row) => ({
        full_name: row.full_name,
        // Every row in this table IS in scope — the table is the scope. The field
        // exists so Lane J can render a list mixing "connected" and "available from
        // your GitHub account" without a second shape, once that second source lands.
        in_scope: true,
      })),
    };
    return respond(body);
  } catch (error) {
    return unhandled(error);
  }
}

export async function PUT(request: Request): Promise<NextResponse> {
  try {
    // THE SAME CROSS-SITE DEFENCE AS THE APPROVALS ROUTE, because this list is an
    // authorisation boundary. A page on another site that could add a repository to
    // your scope has made its runs approvable by you — a slower version of the same
    // attack, and loopback binding does not apply here at all since this is not
    // loopback-bound.
    if (!originIsAcceptable(request.headers.get("origin"), allowedOrigins())) {
      return refuse(
        "this request came from another site's page and was not acted on",
        403,
      );
    }

    const session = await currentIdentity();
    if (session === null) {
      return refuse("sign in to change your repositories", 401);
    }

    const body = await readJson(request);
    if (!body.ok) {
      return body.response;
    }

    const record = body.value as { full_names?: unknown };
    if (!Array.isArray(record.full_names)) {
      return refuse("full_names must be an array of owner/name strings", 400);
    }

    // EVERY ENTRY IS VALIDATED AND A SINGLE BAD ONE REFUSES THE WHOLE REQUEST. Not
    // filtered: silently dropping an invalid name would leave a caller believing they
    // had scoped a repository they had not, and the failure would surface later as an
    // approval refused for a reason nobody could trace back to here.
    const names: string[] = [];
    for (const candidate of record.full_names) {
      if (!isFullName(candidate)) {
        // The offending value is NOT echoed.
        return refuse(
          "every entry must be a repository name of the form owner/name",
          400,
        );
      }
      names.push(candidate);
    }

    // DE-DUPLICATED HERE rather than relying on the UNIQUE constraint to raise. The
    // constraint is `(tenant_id, full_name)`, so a repeated name in one request is an
    // IntegrityError halfway through a batch — some rows written, some not, and the
    // caller told only that it failed.
    const unique = [...new Set(names)];

    const answer = await readPipeline<{ repositories: { full_name: string }[] }>(
      "repositories",
      {
        action: "set_scope",
        tenant_id: session.tenantId,
        full_names: unique,
        // AUDITED. Who changed an authorisation boundary is the same class of fact as
        // who approved a gate, and it comes from the session for the same reason.
        by: session.login,
      },
    );

    const view: RepositoryListResponse = {
      repositories: answer.repositories.map((row) => ({
        full_name: row.full_name,
        in_scope: true,
      })),
    };
    return respond(view);
  } catch (error) {
    return unhandled(error);
  }
}
