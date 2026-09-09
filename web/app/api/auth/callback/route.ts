/**
 * GET /api/auth/callback — where Cognito sends the browser back, with a code.
 *
 * THE ORDER IS THE DESIGN, and it is `infra/ingress/handler.py`'s: everything
 * that costs a network call happens AFTER the checks that cost nothing.
 *
 *   1. `state` cookie missing, `state` parameter missing, or the two differ
 *                                          -> /signin. NO token exchange.
 *   2. no `code`                            -> /signin. NO token exchange.
 *      ── only now is Cognito contacted ──
 *   3. the exchange fails or returns no id_token -> /signin
 *   4. the token we were just handed does not verify -> /signin
 *      ── only now is a cookie set ──
 *   5. cookie, redirect to /runs
 *
 * Step 4 looks redundant and is not. **The exchange succeeding is a different
 * claim from the token being usable**: a token minted by a second app client in
 * the same pool carries a valid signature and the wrong `aud`, a pool at a
 * different issuer verifies against different keys, and an access token has no
 * role claim at all. Writing an unverified token into the cookie would move all
 * three failures to the next request, where they read as "the session broke"
 * rather than "sign-in did not complete".
 *
 * EVERY REFUSAL LANDS ON THE SAME PAGE AND SAYS NOTHING ABOUT WHICH ONE FIRED.
 * That is deliberate: the person who caused it can only act on "sign in again",
 * and the distinctions — a replayed code, a forged `state`, a token for another
 * pool — are exactly what an attacker probing the callback would like enumerated.
 * The cost is stated rather than hidden: an operator debugging a genuinely
 * misconfigured pool sees the same bounce as an attacker, and has to read the
 * Cognito side to tell them apart.
 */

import { NextResponse, type NextRequest } from "next/server";

import {
  OAUTH_STATE_COOKIE,
  SESSION_COOKIE,
  SESSION_MAX_AGE_SECONDS,
  exchangeCode,
  verifySession,
} from "@/lib/cognito";

// Reads `AUTH_URL` and a request URL, and performs a network call. Prerendering
// it would run all three at build time — see `signin/route.ts` for the measured
// consequence in the reference project.
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<Response> {
  const base = process.env.AUTH_URL ?? request.nextUrl.origin;
  const signIn = NextResponse.redirect(new URL("/signin", base));
  // CLEARED ON EVERY PATH, refusal and success alike. A `state` that survives its
  // round trip is a `state` that can be replayed, so it is one-time by
  // construction rather than by the ten-minute expiry alone.
  signIn.cookies.set(OAUTH_STATE_COOKIE, "", {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/api/auth",
    maxAge: 0,
  });

  const expected = request.cookies.get(OAUTH_STATE_COOKIE)?.value ?? "";
  const presented = request.nextUrl.searchParams.get("state") ?? "";
  // BOTH MUST BE NON-EMPTY *AND* EQUAL. Comparing alone is not enough: with the
  // cookie absent and no `state` on the URL, `"" === ""` is `true` and the check
  // passes on a request that carried no evidence at all — the fail-open direction,
  // and the same shape as `originIsAcceptable` refusing every present Origin
  // against an empty allow-list rather than admitting them.
  if (expected === "" || presented === "" || expected !== presented) {
    return signIn;
  }

  const code = request.nextUrl.searchParams.get("code");
  if (!code) {
    return signIn;
  }

  const idToken = await exchangeCode(code, `${base}/api/auth/callback`);
  if (!idToken) {
    return signIn;
  }

  // Verify what we were just handed, before trusting it into a cookie.
  if ((await verifySession(idToken)) === null) {
    return signIn;
  }

  // `/runs` and not `/`: the run list is what a reviewer came for, and `/` is a
  // redirect anyway. A person with no tenant yet still lands here and the screen
  // tells them their account is not attached to an organisation — which is the
  // state requirement 9's self-service sign-up makes the common one, and it must
  // read as "not yet authorised" rather than as an empty run list.
  const response = NextResponse.redirect(new URL("/runs", base));
  response.cookies.set(SESSION_COOKIE, idToken, {
    httpOnly: true, // no script can read it
    secure: true, // https only
    sameSite: "lax", // survives the OAuth redirect, refuses cross-site POSTs
    path: "/",
    // ONE HOUR, and `cognito.SESSION_MAX_AGE_SECONDS` carries the argument: the
    // tenant now rides on this token, so its lifetime IS the revocation latency
    // for removing somebody from an organisation.
    maxAge: SESSION_MAX_AGE_SECONDS,
  });
  // The `state` is spent whether or not sign-in succeeded.
  response.cookies.set(OAUTH_STATE_COOKIE, "", {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/api/auth",
    maxAge: 0,
  });
  return response;
}
