/**
 * GET /api/github/repositories — the repositories a person can PICK, not type.
 *
 * Reported from the deployed app: *"i need dropdown i dont want to write any"*. The
 * repositories screen asked for `owner/name` in a text field, which means knowing the
 * exact spelling, leaving the product to go and check it, and getting a run scoped to
 * a repository that does not exist if a character is wrong.
 *
 * **THIS IS THE `honest fix` `app/api/repositories/route.ts` NAMES AND COULD NOT
 * MAKE.** That file says, about OAuth's `repo` scope: *"it is all-or-nothing across
 * every repository the person can reach, and there is no per-repository OAuth scope
 * … The honest fix is a GitHub App."* There is now a GitHub App, so the set below is
 * the one the person chose at INSTALL time — not everything their account can see.
 *
 * ## Why it reads the GitHub session directly
 *
 * `currentIdentity()` deliberately returns only `login` and `tenantId`: it is what
 * `authz.decide` consumes, and widening it to carry a credential would put a GitHub
 * token into every route that merely wants to know who is asking. The token lives in
 * the encrypted GitHub session cookie and is read here, at the one place that needs it.
 *
 * ## An empty list is a REAL ANSWER and is not an error
 *
 * Two different empties, and they must not collapse — `scan_provenance`'s rule:
 *
 *   * signed in with GitHub, app installed nowhere  -> `[]` with `linked: true`
 *   * signed in with EMAIL, so there is no token    -> `[]` with `linked: false`
 *
 * The first is fixed by installing the app; the second by signing in with GitHub.
 * Rendering both as "no repositories found" sends somebody to the wrong remedy, and
 * rendering either as a failure sends them to a retry that cannot help.
 */

import { NextResponse } from "next/server";
import { cookies } from "next/headers";

import { refuse, respond } from "@/lib/http";
import { currentIdentity } from "@/lib/session";
import { GITHUB_SESSION_COOKIE, readSession } from "@/lib/github-session";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  try {
    // AUTHENTICATED FIRST. This lists repositories a person can reach on GitHub;
    // an unauthenticated caller must not be able to drive GitHub API calls from
    // this deployment's address at all.
    const session = await currentIdentity();
    if (session === null) {
      return refuse("sign in to see your repositories", 401);
    }

    const jar = await cookies();
    const github = await readSession(jar.get(GITHUB_SESSION_COOKIE)?.value);
    if (github === null) {
      // Signed in, but not with GitHub — so this application holds no token for
      // them. Not a refusal: the screen still works, it just cannot offer a list.
      return respond({ repositories: [], linked: false });
    }

    const { installationRepositories } = await import("@/lib/github-oauth");
    const repositories = await installationRepositories(github.accessToken);
    return respond({ repositories, linked: true });
  } catch (error) {
    // A GITHUB OUTAGE MUST NOT BREAK THE SCREEN, because the text field beside this
    // list still works. So this degrades to an empty list rather than a 500 -- a
    // failure here makes an optional convenience look load-bearing.
    //
    // **`unavailable` IS A THIRD STATE AND NOT A SECOND SPELLING OF EMPTY.** The
    // three answers want three different remedies and the screen says which:
    //
    //     linked: false                 -> sign in with GitHub
    //     linked: true,  []             -> install the app on a repository
    //     unavailable: true             -> GitHub did not answer; type the name
    //
    // Collapsing the third into the second tells somebody to install an app they
    // have already installed. Same argument as `fixture-fallback` (a FAULT) never
    // sharing a spelling with `fixture-stub` (a CHOICE).
    console.error("[api/github/repositories] could not list installations", error);
    return respond({ repositories: [], linked: true, unavailable: true });
  }
}
