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
import { cookies } from "next/headers";

import { GITHUB_SESSION_COOKIE, readSession } from "@/lib/github-session";
import { currentIdentity, verifiedToken } from "@/lib/session";
import { tenantFromClaim } from "@/lib/tenant";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  try {
    const token = await verifiedToken();
    const identity = await currentIdentity();

    /**
     * **THE THIRD READ, AND ITS ABSENCE BROKE `/account` COMPLETELY.** Reported
     * from the deployed app: *"how come sign out is available and I see runs"* on
     * a page saying **"Nobody is signed in"**. Every other surface worked —
     * `currentIdentity()` had already been taught the GitHub session, so the runs
     * loaded and the nav rendered — and this route still answered
     * `signed_in: false`, because `verifiedToken()` reads the COGNITO cookie and
     * nothing else.
     *
     * A route whose entire job is "who is signed in" was the one place that did
     * not know. The lesson is narrow and worth keeping: **teaching
     * `currentIdentity()` a second session did not teach the surfaces that read
     * authentication DIRECTLY**, and this one deliberately bypasses it in order to
     * tell signed-in-but-unauthorised apart from signed-out.
     */
    const jar = await cookies();
    const github = await readSession(jar.get(GITHUB_SESSION_COOKIE)?.value);

    const view: SessionView = {
      // EITHER SESSION. The three states this route keeps apart are unchanged;
      // there are simply two ways to reach the first of them now.
      signed_in: token !== null || github !== null,
      // The pool's `cognito:username`, or the GitHub login. It is what
      // `HumanDecision.by` will read, so showing it is showing a person the name a
      // gate decision would carry.
      login: token?.login ?? github?.login ?? null,
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
      // **THIS WAS HARDCODED `false`, AND THAT STOPPED BEING TRUE.** The comment
      // here argued, correctly at the time, that the field meant "an Auth.js
      // `accounts` row holds a GitHub access token", that removing the GitHub
      // OAuth provider removed the only writer of that row, and that reporting
      // `true` would render a "linked" mark for a credential that is not there —
      // "a check present, enumerable, and backed by nothing".
      //
      // Every word of that was right until GitHub sign-in shipped. There IS a
      // GitHub grant now: it is the encrypted session cookie, and the token in it
      // is what `/api/github/repositories` lists installations with. So the field
      // means what it always claimed to, and the honest value is the live one.
      //
      // **A HARDCODED FALSE IS A CLAIM WITH A SHELF LIFE**, and it reads as
      // permanent because a constant has no date on it. This one survived exactly
      // as long as the sentence explaining it.
      github_linked: github !== null,
    };

    return respond(view);
  } catch (error) {
    return unhandled(error);
  }
}
