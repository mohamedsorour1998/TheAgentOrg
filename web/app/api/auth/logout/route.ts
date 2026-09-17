/**
 * GET /api/auth/logout — sign out on BOTH sides.
 *
 * Clearing this application's cookie is only half of it. Cognito keeps its own
 * session cookie on the sign-in domain, so a reviewer who "signed out" and
 * clicked sign in again would be returned straight to `/runs` without being
 * asked for anything — which reads as the sign-out having silently failed. This
 * clears the local cookie **and** redirects to Cognito's `/logout`, which clears
 * theirs and then sends the browser to a registered logout URL.
 *
 * `logout_uri` MUST BE ONE OF THE APP CLIENT'S `LogoutURLs` or Cognito refuses
 * the request outright — a Lane Q provisioning requirement, and one whose failure
 * is visible (an error page) rather than silent.
 *
 * A GET RATHER THAN A POST, DELIBERATELY, and it is the one place this
 * application's POST-only-mutations rule is knowingly not applied. `POST
 * /api/approvals` is POST-only because it RECORDS a decision and a GET is
 * reachable by a back button, a bookmark or a prefetch. This route destroys
 * nothing a person would miss: the worst a prefetch can do is sign somebody out,
 * which is the fail-safe direction, and a link is what a navigation bar can
 * offer. If this route ever gains a side effect that is not "end a session", that
 * reasoning stops holding.
 *
 * IT FAILS **OPEN**, AND THAT IS THE OPPOSITE OF EVERY OTHER REFUSAL IN THIS
 * LANE, ON PURPOSE. With Cognito unconfigured the local cookie is still cleared
 * and the browser still lands somewhere sensible. A sign-out that threw would
 * leave the session intact — the one outcome this route must never produce — and
 * here "open" means *signed out*. The distinction that reconciles it with
 * everything else: fail closed on a question about AUTHORITY, fail open on a
 * question about ENDING one.
 */

import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE, logoutUrl } from "@/lib/cognito";
import { GITHUB_SESSION_COOKIE } from "@/lib/github-session";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<Response> {
  const base = process.env.AUTH_URL ?? request.nextUrl.origin;
  // `/signin` rather than `/`: a person who has just signed out should see the
  // sign-in screen, not a page that looks signed-out-but-idle.
  const landing = `${base}/signin`;

  let target = landing;
  try {
    target = logoutUrl(landing);
  } catch {
    // COGNITO_DOMAIN or COGNITO_CLIENT_ID is unset. Clear the local cookie
    // anyway and land locally — see the fail-open note above.
  }

  const response = NextResponse.redirect(target);
  // THE SAME ATTRIBUTES THE CALLBACK SET IT WITH. A cookie deleted with a
  // different `path` or `sameSite` is a cookie that SURVIVES — the browser
  // matches on those, not on the name alone, and the symptom is a sign-out that
  // reports success while the session continues. That is this repository's
  // signature failure shape, in a cookie jar.
  response.cookies.set(SESSION_COOKIE, "", {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });

  // THE GITHUB SESSION TOO. There are two sign-in paths and two cookies, and
  // clearing one is a sign-out that reports success while the other session
  // continues -- which is worse than not offering sign-out at all, because the
  // person believes they have ended it. Cleared UNCONDITIONALLY rather than only
  // when present: a `set` with `maxAge: 0` on an absent cookie is a no-op, while a
  // condition is a branch that can be wrong.
  //
  // The Cognito `/logout` redirect above does nothing for this one -- GitHub's own
  // authorisation also survives, at
  // github.com/settings/connections/applications/<client-id>, exactly as
  // `/api/link/github` already says about revocation it cannot reach.
  response.cookies.set(GITHUB_SESSION_COOKIE, "", {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return response;
}
