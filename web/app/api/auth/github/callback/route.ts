/**
 * GET /api/auth/github/callback — finish a GitHub sign-in.
 *
 * **THE URL GITHUB SENDS PEOPLE BACK TO**, and it must match the App's *Redirect
 * URI* field byte for byte. A GitHub App calls that field "Redirect URI" where an
 * OAuth App calls it "Authorization callback URL" — the same thing under two names,
 * and the reason the operator could not find the field I had named.
 *
 * The order below is the ingress Lambda's order, for the ingress Lambda's reason:
 * **everything that costs anything happens after the checks that cost nothing.**
 *
 *     1. an `error` from GitHub        -> /signin, no token exchange attempted
 *     2. no code, or no state          -> /signin
 *     3. state does not match the cookie -> /signin   <- CSRF, refused here
 *     ---- only now is the client secret used ----
 *     4. exchange the code, read the user
 *     5. mint a session, clear the state cookie
 *
 * Step 3 is the whole point of step 1 in `/api/auth/github`. A flow that MINTS state
 * and never compares it is indistinguishable from this one in a browser, and defends
 * nothing: an attacker can hand somebody a callback URL carrying their own code and
 * silently sign that person into the attacker's account.
 *
 * **EVERY REFUSAL LANDS ON `/signin`, NOT ON A JSON ERROR.** This is a browser
 * redirect target, reached by a person and not by `fetch` — an error object rendered
 * as raw JSON reads as a crash. The reason travels as a short, non-identifying code.
 */

import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE } from "@/lib/cognito";
import { appOrigin, exchangeCode, originIsSecure } from "@/lib/github-oauth";
import {
  GITHUB_SESSION_COOKIE,
  MAX_AGE_SECONDS,
  mintSession,
  tenantForGitHub,
} from "@/lib/github-session";
import { STATE_COOKIE } from "../route";

export const dynamic = "force-dynamic";

function back(request: NextRequest, reason: string): NextResponse {
  const url = new URL(`/signin?error=${reason}`, appOrigin(request.nextUrl.origin));
  const response = NextResponse.redirect(url);
  // THE STATE COOKIE IS CLEARED ON EVERY EXIT, success or failure. A state left
  // behind is a value an attacker gets a second attempt against, and a stale one
  // makes the NEXT sign-in fail for a reason nobody can see.
  response.cookies.set(STATE_COOKIE, "", { path: "/api/auth", maxAge: 0 });
  return response;
}

export async function GET(request: NextRequest): Promise<Response> {
  const params = request.nextUrl.searchParams;

  // 1. GitHub refused, or the person clicked Cancel. No exchange is attempted.
  if (params.get("error")) {
    return back(request, "github_denied");
  }

  // 2. Both must be present before anything is spent.
  const code = params.get("code");
  const state = params.get("state");
  const expected = request.cookies.get(STATE_COOKIE)?.value;
  if (!code || !state || !expected) {
    return back(request, "github_state");
  }

  // 3. CSRF. Compared BEFORE the client secret is read, so an unauthenticated
  //    caller cannot drive Secrets Manager reads against this endpoint -- the
  //    ingress handler's rule, where steps 1-3 precede the secret fetch entirely.
  if (state !== expected) {
    return back(request, "github_state");
  }

  let identity;
  try {
    identity = await exchangeCode(code);
  } catch (error) {
    // The DETAIL is logged, never redirected onto the URL: it can name a secret id
    // or echo GitHub's own description of a credential failure.
    console.warn("[auth/github/callback] exchange failed:", (error as Error).message);
    return back(request, "github_exchange");
  }

  let cookieValue: string;
  try {
    cookieValue = await mintSession({
      login: identity.login,
      tenantId: tenantForGitHub(identity.login, identity.id),
      accessToken: identity.accessToken,
    });
  } catch (error) {
    console.warn("[auth/github/callback] could not mint a session:", (error as Error).message);
    return back(request, "github_session");
  }

  const response = NextResponse.redirect(new URL("/runs", appOrigin(request.nextUrl.origin)));
  response.cookies.set(GITHUB_SESSION_COOKIE, cookieValue, {
    httpOnly: true,
    secure: originIsSecure(request.nextUrl.origin),
    sameSite: "lax",
    path: "/",
    maxAge: MAX_AGE_SECONDS,
  });
  response.cookies.set(STATE_COOKIE, "", { path: "/api/auth", maxAge: 0 });

  // THE COGNITO COOKIE IS CLEARED TOO, and this is not tidiness. Both cookies are
  // session-bearing and `currentIdentity` consults them in a fixed order, so a
  // person who signed in with email and then with GitHub would keep acting as
  // whichever one the order happens to prefer -- signing in and staying somebody
  // else. One sign-in, one session.
  response.cookies.set(SESSION_COOKIE, "", { path: "/", maxAge: 0 });

  return response;
}
