/**
 * GET /api/session — who is signed in, and which tenant the server resolved.
 *
 * UNAUTHENTICATED BY NECESSITY: its whole answer may be "nobody is signed in". Every
 * other route refuses with 401; this one answers 200 with `signed_in: false`, because
 * a screen asking "should I show a sign-in button?" must not have to read a 401 as
 * data.
 *
 * NO TOKEN, EVER. The GitHub access token in `accounts.access_token` carries the
 * `repo` scope and can act on every repository the person can reach. A route that
 * sent it to a browser would hand a repository-wide credential to client JavaScript.
 * The fields below are the complete set, and none of them is a credential.
 */

import { NextResponse } from "next/server";

import type { SessionView } from "@/lib/endpoints";
import { respond, unhandled } from "@/lib/http";
import { auth } from "@/lib/auth";
import { currentIdentity } from "@/lib/session";

export async function GET(): Promise<NextResponse> {
  try {
    const raw = await auth();
    // TWO READS, DELIBERATELY, because they answer different questions and can
    // disagree. `auth()` says whether a session cookie is valid; `currentIdentity()`
    // says whether that person resolves to a tenant. A person who has signed in but
    // belongs to no organisation is `signed_in: true, tenant_id: null` — which is
    // exactly the state Lane J needs to render "your account is not attached to an
    // organisation" rather than bouncing them back to sign in.
    const identity = await currentIdentity();

    const view: SessionView = {
      signed_in: Boolean(raw?.user),
      login: identity?.login ?? raw?.user?.name ?? null,
      name: raw?.user?.name ?? null,
      image: raw?.user?.image ?? null,
      tenant_id: identity?.tenantId ?? null,
      // A GitHub grant exists iff Auth.js produced a session at all — GitHub is the
      // only provider configured, so signing in IS linking. That is spec §11's
      // "collapses sign-in and link account into one flow instead of two", and this
      // field reads as `true` for the same reason rather than by coincidence.
      github_linked: Boolean(raw?.user),
    };

    return respond(view);
  } catch (error) {
    return unhandled(error);
  }
}
