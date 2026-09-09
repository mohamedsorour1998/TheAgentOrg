/**
 * GET /api/session — who is signed in, and which tenant the server resolved.
 *
 * UNAUTHENTICATED BY NECESSITY: its whole answer may be "nobody is signed in".
 * Every other route refuses with 401; this one answers 200 with
 * `signed_in: false`, because a screen asking "should I show a sign-in button?"
 * must not have to read a 401 as data.
 *
 * NO TOKEN, EVER. The ID token in the cookie is a bearer credential for this
 * application — a route that returned it would hand a browser a value that opens
 * a security gate. None of the fields below is a credential, and `sub` is
 * deliberately absent too: nothing on a screen needs the opaque subject, and a
 * field nobody renders is a field that leaks into a log for no gain.
 *
 * =========================================================================
 * THE THREE STATES THIS ROUTE MUST KEEP APART, AND WHY IT TAKES TWO READS
 * =========================================================================
 * Self-service sign-up is allowed (requirement 9), so an account can exist and
 * be authorised for nothing. Three states, and collapsing any pair produces a
 * screen that lies:
 *
 *   `signed_in: false`                     nobody is signed in. Offer sign-in.
 *   `signed_in: true,  tenant_id: null`    signed in, NOT YET AUTHORISED. An
 *                                          administrator assigns a role and a
 *                                          tenant. Every authenticated route
 *                                          refuses until then, correctly.
 *   `signed_in: true,  tenant_id: "..."`   a reviewer. Show the runs.
 *
 * **The middle one is the new common case and the screen must say so in words.**
 * A blank run list or "no runs yet" for an account nobody has assigned is this
 * repository's signature conflation — "did not run" reading as "passed" — on the
 * front door. `web/components/` is Lane J's, so this route's job is to make the
 * state legible rather than to render it: `signed_in && tenant_id === null` is
 * the exact predicate, and it needs no new contract field.
 *
 * Hence TWO reads. `verifiedToken()` answers "is this token authentic" and
 * `currentIdentity()` answers "may this account act" — and they can disagree, on
 * every account between sign-up and assignment.
 */

import { NextResponse } from "next/server";

import type { SessionView } from "@/lib/endpoints";
import { respond, unhandled } from "@/lib/http";
import { currentIdentity, verifiedToken } from "@/lib/session";
import { tenantFromClaim } from "@/lib/tenant";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  try {
    const token = await verifiedToken();
    const identity = await currentIdentity();

    const view: SessionView = {
      signed_in: token !== null,
      // The pool's `cognito:username`. It is what `HumanDecision.by` will read,
      // so showing it is showing a person the name a gate decision would carry.
      login: token?.login ?? null,
      // NULL, ALWAYS, AND THAT IS HONEST RATHER THAN UNFINISHED. `cognito.ts`
      // carries neither `name` nor `email` into the identity, because inbound JWT
      // claims are logged by AWS outside every redaction this application has.
      // `AccountPanel` already renders "This account set no display name."
      name: null,
      // NULL for the same reason plus one more: there is no avatar host in this
      // design at all. Cognito issues no picture claim and nothing fetches one.
      image: null,
      // The VALIDATED claim, never the raw one — `tenantFromClaim` refuses blank,
      // over-long and control characters, so a screen cannot render a malformed
      // tenant as though it were a scope. `currentIdentity` applies the same
      // function, so the two agree by construction rather than by coincidence.
      tenant_id: identity?.tenantId ?? tenantFromClaim(token?.tenantId ?? null),
      // FALSE, ALWAYS, AND IT IS A TRUE STATEMENT ABOUT A CAPABILITY THAT NO
      // LONGER EXISTS. This field meant "an Auth.js `accounts` row holds a GitHub
      // access token". Removing the GitHub OAuth provider removed the only writer
      // of that row, so this deployment holds no GitHub grant for anybody.
      //
      // Reporting `true` would render a "linked" mark on `/account` for a
      // credential that is not there — a check present, enumerable, and backed by
      // nothing, which is the exact shape `pg_policies` takes under a superuser.
      // The cost of `false` is stated rather than hidden: `SignInPanel` renders
      // "Continuing with GitHub again restores it", which is now a remedy that
      // does not work, and `AccountPanel`'s GitHub-link section describes a
      // capability this deployment does not have. Both are Lane J's files and
      // both are named in this lane's report.
      github_linked: false,
    };

    return respond(view);
  } catch (error) {
    return unhandled(error);
  }
}
