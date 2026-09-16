/**
 * SIGN IN FIRST. The gate every dashboard screen passes through.
 *
 * **THE PRODUCT SHOWED THE DASHBOARD TO A SIGNED-OUT VISITOR**, reported from the
 * deployed app: *"when user open theagentorg website it need to login via github
 * then he can see the dashboard, not see dashboard then he can login"*. Opening the
 * site rendered `/runs` — heading, nav, table frame and all — and only the data
 * inside it came back refused. The screen was built, the person was not signed in,
 * and the two facts never met.
 *
 * `app/page.tsx` even documented the behaviour that did not exist: *"a signed-out
 * visitor is sent on to `/signin` by the runs screen itself, which is where that
 * decision belongs"*. Correct about where it belongs; the runs screen never did it.
 * **A comment describing a guard is not a guard**, and this repository has now found
 * that in a workflow expression, in a test satisfied by prose, and here.
 *
 * ## This is a NAVIGATION gate, not the security boundary
 *
 * Stated plainly because the distinction decides what may be built on it. Every
 * route under `app/api/**` already refuses on its own through `authz.decide`, and
 * that is what protects the DATA. This function decides what a browser is shown.
 * A person who forges a cookie gets a rendered shell and a screenful of 401s — so
 * nothing here may be relied on to keep anybody out of anything, and no route may
 * drop its own check because this exists.
 *
 * ## The redirect loop, refused by construction
 *
 * `currentIdentity()` returns `null` for FOUR situations and collapses them
 * deliberately — no cookie, a token that fails verification, an account with no
 * admitted role, and an account with no usable tenant. Redirecting all four to
 * `/signin` builds an infinite loop for the last two: the person signs in, arrives
 * back, still has no tenant, and is sent to sign in again — while genuinely being
 * signed in.
 *
 * So this asks BOTH layers. `verifiedToken()` answers "is anybody there", and the
 * two cases get different destinations:
 *
 *     no token at all          -> /signin              ("sign in")
 *     a token, but no workspace -> /signin?account=unassigned  ("you are signed in,
 *                                  and this account has no workspace yet")
 *
 * Neither loops, because `/signin` is never guarded. The second is reachable today
 * only for an account provisioned without a tenant — self-service sign-up assigns
 * one at creation — which makes it exactly the kind of branch that rots unseen, so
 * it is one URL rather than a second code path.
 */

import { redirect } from "next/navigation";

import type { SessionIdentity } from "./authz";
import { currentIdentity, verifiedToken } from "./session";

/** Where a visitor with no session is sent. */
export const SIGN_IN_PATH = "/signin";

/** Where a verified account with no tenant or role is sent. */
export const UNASSIGNED_PATH = "/signin?account=unassigned";

/**
 * The signed-in identity, or a redirect — this never returns `null`.
 *
 * `redirect()` throws a control-flow error Next.js catches, so it must NOT be
 * wrapped in a `try`. A caller that swallowed it would render the page to a
 * signed-out visitor with the redirect silently absorbed, which is this
 * repository's signature defect wearing a framework's clothes.
 */
export async function requireIdentity(): Promise<SessionIdentity> {
  const identity = await currentIdentity();
  if (identity !== null) {
    return identity;
  }

  // ASKED SECOND, and only when the first answer was no. `verifiedToken` performs
  // the signature check, so calling it unconditionally would verify twice on every
  // authenticated page load for a distinction only the refusal path needs.
  const token = await verifiedToken();
  redirect(token === null ? SIGN_IN_PATH : UNASSIGNED_PATH);
}
