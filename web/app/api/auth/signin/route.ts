/**
 * GET /api/auth/signin — start the Cognito hosted-UI round trip.
 *
 * A NAVIGATION, SO A GET, and that is not the exception to this application's
 * POST-only-mutations rule — it is outside it. Nothing here mutates: it mints a
 * one-time `state`, remembers it in a short-lived cookie, and redirects. A back
 * button, a bookmark or a prefetch reaching this route costs one wasted `state`
 * and nothing else, which is `approve_server.do_GET`'s standard: a GET must be
 * inert when it is replayed.
 *
 * **THE BASE ORIGIN COMES FROM `AUTH_URL`, AND THE NAME IS DELIBERATELY NOT
 * CHANGED.** It is Auth.js's spelling and Auth.js is gone, so renaming it to
 * something Cognito-flavoured is tempting — and would be two declarations of one
 * fact. `web/lib/origins.ts` derives the CSRF origin allow-list from this exact
 * variable, and its own header records the consequence of a second list: "when
 * they drift the symptom is a legitimate click being refused, which reads as a
 * broken button rather than as a misconfiguration." One value, one name, both
 * halves.
 *
 * `?next=` IS NOT ACCEPTED, and its absence is the point. An open redirect on the
 * route that establishes a session is how a phishing page borrows this
 * application's domain, and there is exactly one place worth landing after
 * sign-in. The callback sends everybody to `/runs`.
 */

import { NextResponse, type NextRequest } from "next/server";

import { OAUTH_STATE_COOKIE, hostedUiUrl } from "@/lib/cognito";

// FORCE-DYNAMIC, AND WITHOUT IT THE BUILD FAILS RATHER THAN THIS ROUTE. Next
// prerenders a handler with no dynamic API by default, and `hostedUiUrl` throws
// on an absent `COGNITO_DOMAIN` — measured in the reference project, where the
// equivalent page took the whole build down with `Export encountered an error`.
// The quieter half is why this is the right fix rather than supplying the
// variable at build time: with it present the redirect URL, client id included,
// bakes into the bundle, so rotating the app client keeps sending people to the
// old one until somebody rebuilds. A sign-in redirect is request-time
// configuration.
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<Response> {
  const base = process.env.AUTH_URL ?? request.nextUrl.origin;

  // `crypto.randomUUID` is the Web Crypto one on Next's runtimes — 122 bits from
  // a CSPRNG. `Math.random()` would be the mistake worth naming: it is seeded
  // per-process and predictable, and a predictable `state` is no `state` at all.
  const state = crypto.randomUUID();

  let target: string;
  try {
    target = hostedUiUrl({ redirectUri: `${base}/api/auth/callback`, state });
  } catch {
    // `COGNITO_DOMAIN` or `COGNITO_CLIENT_ID` is unset. Redirecting to the sign-in
    // screen would loop; a 500 with a sentence naming the cause is the honest
    // answer, and it names the VARIABLE rather than echoing any value.
    return new NextResponse(
      "sign-in is not configured: COGNITO_DOMAIN and COGNITO_CLIENT_ID must both " +
        "be set on this deployment. Nothing was recorded.",
      { status: 500, headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  }

  const response = NextResponse.redirect(target);
  response.cookies.set(OAUTH_STATE_COOKIE, state, {
    httpOnly: true, // no script can read it
    secure: true, // https only — and browsers treat http://localhost as secure
    // `lax` and not `strict`: the callback arrives as a top-level navigation FROM
    // Cognito's domain, and `strict` withholds the cookie on exactly that
    // cross-site GET, so every sign-in would fail the state check it exists to
    // pass. `lax` still refuses a cross-site POST, which is the threat.
    sameSite: "lax",
    path: "/api/auth",
    // Ten minutes. Long enough for a person to type a password and a one-time
    // code, short enough that a stale value in a shared browser is not a session.
    maxAge: 600,
  });
  return response;
}
