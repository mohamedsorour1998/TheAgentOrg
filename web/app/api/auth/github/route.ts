/**
 * GET /api/auth/github — start a GitHub sign-in.
 *
 * Mints a `state`, puts it in an `HttpOnly` cookie, and redirects to GitHub. The
 * callback compares the two. **STATE THAT IS SENT AND NEVER COMPARED DEFENDS
 * NOTHING** and looks identical in a browser — the same shape as the existence
 * oracle this repository already found, where the message matched and the status
 * code still differed. The comparison lives in the callback and is the reason this
 * cookie exists at all.
 *
 * `force-dynamic` for the reason `/api/auth/signin` carries it: without it the
 * redirect URL — including the client id and the redirect URI — is baked into the
 * bundle at build time, so rotating the GitHub App would keep sending people to the
 * old one until somebody rebuilt. A sign-in redirect is request-time configuration,
 * not build-time content.
 *
 * **IT FAILS CLOSED, and the failure is VISIBLE rather than a blank redirect.** If
 * the secret is missing the person is returned to `/signin?error=github` rather than
 * bounced to GitHub with a malformed request, because GitHub's own error page reads
 * as "this application is broken" and sends the next person looking in the wrong
 * place entirely.
 */

import { NextResponse, type NextRequest } from "next/server";

import { appOrigin, authorizeUrl, originIsSecure } from "@/lib/github-oauth";

export const dynamic = "force-dynamic";

/** Distinct from the Cognito flow's `agentorg_oauth_state`: two flows, two states. */
export const STATE_COOKIE = "agentorg_gh_state";

/** Ten minutes: long enough to sign in, short enough not to linger. */
const STATE_MAX_AGE = 600;

export async function GET(request: NextRequest): Promise<Response> {
  const state = crypto.randomUUID();

  let target: string;
  try {
    target = await authorizeUrl(state);
  } catch (error) {
    const back = new URL("/signin?error=github", appOrigin(request.nextUrl.origin));
    // The REASON is not put on the URL: it names a secret id and a configuration
    // state, which is information an unauthenticated visitor has no use for.
    console.warn("[auth/github] refused to start:", (error as Error).message);
    return NextResponse.redirect(back);
  }

  const response = NextResponse.redirect(target);
  response.cookies.set(STATE_COOKIE, state, {
    httpOnly: true,
    // `secure` follows the origin: a bare-http localhost stack would silently drop
    // a `secure` cookie and the callback would then refuse every sign-in with a
    // state mismatch -- a working flow reading as a CSRF failure.
    secure: originIsSecure(request.nextUrl.origin),
    sameSite: "lax",
    // SCOPED TO THE AUTH PATH, not the whole site: this cookie is only ever read by
    // the callback, and a cookie sent on every request is a cookie with more
    // opportunities to leak.
    path: "/api/auth",
    maxAge: STATE_MAX_AGE,
  });
  return response;
}
