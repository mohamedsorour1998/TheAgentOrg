/**
 * SIGN IN BEFORE THE DASHBOARD — the gate that covers every screen, including the
 * two that cannot gate themselves.
 *
 * Reported from the deployed app: opening the site rendered `/runs` in full to a
 * signed-out visitor — heading, nav, table frame — and only the data inside came
 * back refused. `lib/guard.ts` fixes that for SERVER pages. It cannot fix `costs`
 * or `runs/[runId]`, which are `"use client"`: a client component runs in the
 * browser and cannot call a server guard, so for those two this file is the only
 * thing standing in front.
 *
 * **THE FILE IS `proxy.ts`, NOT `middleware.ts`, AND THE EXPORT IS `proxy`.** Next
 * 16.3.3 deprecated the `middleware` convention and prints `⚠ The "middleware" file
 * convention is deprecated. Please use "proxy" instead.` on every build — measured
 * here, on the first build after writing it the other way. Clean output rather than
 * a zero exit code is this project's bar. **Never have both files: that is a hard
 * error, not a warning** (recorded by the reference deployment, which hit it).
 *
 * ## WHAT THIS CHECKS, AND WHAT IT DELIBERATELY DOES NOT
 *
 * **It checks that a session cookie is PRESENT. It does not verify it.** Verifying
 * means `jwtVerify` against Cognito's published keys, which is a network fetch to
 * the JWKS endpoint from the edge runtime; `jose` caches it, but a cold or failed
 * fetch would answer "not signed in" for a **valid** session — signing everybody out
 * because a key server was briefly slow. A gate whose failure mode is locking out
 * legitimate users is worse than one that renders an empty shell.
 *
 * It does not need to be authoritative, because **it is not the security
 * boundary**. Every route under `app/api/**` verifies the token itself through
 * `authz.decide`, and that is what protects the data; every server page verifies
 * through `lib/guard.ts`. This decides what a browser is SHOWN. A forged cookie gets
 * a rendered dashboard full of 401s, and that is accepted behaviour rather than an
 * oversight — but **no API route may drop its own check because this file exists**,
 * or this cookie test silently becomes the whole authorization model.
 */

import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE } from "@/lib/cognito";
import { GITHUB_SESSION_COOKIE } from "@/lib/github-session";

export function proxy(request: NextRequest) {
  // EITHER KIND OF SESSION. There are two sign-in paths and they produce different
  // cookies: Cognito's ID token, and a GitHub session (Cognito cannot federate
  // GitHub, which is OAuth2 and issues no `id_token`). Checking only the first
  // would redirect every GitHub sign-in straight back to `/signin` — a completed
  // sign-in that reads as a failed one, and an infinite loop from the person's
  // side, because the callback would keep succeeding.
  if (request.cookies.get(SESSION_COOKIE) || request.cookies.get(GITHUB_SESSION_COOKIE)) {
    return NextResponse.next();
  }

  const destination = new URL("/signin", request.url);
  // A REDIRECT, not a rewrite: the address bar must end up on `/signin`, so a
  // reload does not re-attempt a page the person cannot see, and the history holds
  // the screen they actually got.
  return NextResponse.redirect(destination);
}

export const config = {
  /**
   * DEFAULT-DENY: everything is gated except what is named here.
   *
   * The first version of this file listed the four dashboard screens explicitly,
   * which has exactly the wrong default for a private product — **a screen added
   * later is ungated on arrival**, and an ungated screen looks perfectly healthy
   * because it renders. Every route in this application is private; the exceptions
   * are few and knowable, so they are the list.
   *
   * **ANCHORED ON A SEGMENT BOUNDARY — `signin$` and `signin/`, never bare
   * `signin`.** A negative lookahead on a bare prefix matches a PREFIX, so
   * `/signinx` would slip past. Measured by the reference deployment on its own
   * matcher; it costs one character per alternative to close.
   *
   * `api/` is excluded ENTIRELY, and that is not laziness. Those routes answer
   * `401` with a JSON body that every `fetch` caller parses; redirecting them to an
   * HTML sign-in page turns a precise refusal into an unparseable 200, and the
   * caller reads success. The API defends itself and must be allowed to.
   *
   * `/signin` is excluded for the reason `guard.ts` refuses on the other axis:
   * sending a signed-out visitor to a page that redirects signed-out visitors is an
   * infinite loop.
   */
  matcher: [
    // `signup` JOINED THE LIST when register became its own page. Without it a
    // person with no account is redirected to `/signin` the instant they click
    // "Create an account" -- the one screen they cannot use bouncing them off the
    // one screen they need. Anchored like `signin`: `signup$|signup/`, never bare,
    // or `/signupx` slips past as a prefix match.
    "/((?!signin$|signin/|api/|_next/static/|_next/image/|favicon\\.ico$).*)",
  ],
};
