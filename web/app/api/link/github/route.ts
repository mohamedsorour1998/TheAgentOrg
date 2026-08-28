/**
 * DELETE /api/link/github — revoke the GitHub grant. Task I2's third verb.
 *
 * =========================================================================
 * WHAT REVOCATION HERE DOES AND DOES NOT DO. READ BOTH LISTS.
 * =========================================================================
 * DOES: deletes the `accounts` row holding the access token, and deletes every
 * `sessions` row for that user — `ON DELETE CASCADE` in `web/lib/schema.sql` makes the
 * second follow the first when the user goes, and this route deletes both explicitly
 * so it does not depend on cascade ordering. The person is signed out everywhere,
 * immediately, and this application holds no token for them.
 *
 * **This is only possible because sessions are in the DATABASE.** A JWT session is
 * self-contained and cannot be revoked before it expires, so on a JWT strategy this
 * endpoint would delete a row nothing reads while the cookie kept working for thirty
 * days — a revocation button that reports success and revokes nothing. That is why
 * `authConfig` sets `strategy: "database"`, and why the two are one decision.
 *
 * DOES NOT: revoke the grant at GitHub's end. GitHub's OAuth authorisation survives
 * until the person removes it at
 * `github.com/settings/connections/applications/<client-id>`, or until an
 * app-authenticated `DELETE /applications/{client_id}/grant` is called — which needs
 * the CLIENT SECRET as basic auth, not the user's token.
 *
 * That call is deliberately NOT made here, and the reason is not laziness: it would
 * put the OAuth client secret into a request path reachable from a browser session,
 * on the same process that can approve a security gate. The narrower, honest
 * behaviour is to forget the token and TELL the person where to complete the
 * revocation — which the response does, because a revocation that silently leaves the
 * grant standing is the reassuring non-answer this repository refuses.
 *
 * DOES NOT: remove repositories from tenant scope. Those rows are referenced by runs
 * and `accessors` exposes no delete — see `web/lib/reader/repositories.py`. So a
 * re-linked account finds its scope intact, which is the behaviour a person expects
 * and is worth stating rather than discovering.
 */

import { NextResponse } from "next/server";

import { originIsAcceptable } from "@/lib/authz";
import { refuse, respond, unhandled } from "@/lib/http";
import { allowedOrigins } from "@/lib/origins";
import { auth, sessionPool } from "@/lib/auth";

export async function DELETE(request: Request): Promise<NextResponse> {
  try {
    // A CROSS-SITE DELETE MUST NOT LAND, even though its effect is to REMOVE access
    // rather than grant it. The attack is denial: a page on another site that could
    // sign a reviewer out mid-demo, repeatedly, is a way to stop a gate being
    // approved — and refusing an approval is as much a decision as making one.
    if (!originIsAcceptable(request.headers.get("origin"), allowedOrigins())) {
      return refuse(
        "this request came from another site's page and was not acted on",
        403,
      );
    }

    const session = await auth();
    const userId = session?.user?.id;
    if (!userId) {
      // 401 rather than a cheerful 200. "You were not signed in, so nothing was
      // revoked" is a different fact from "your grant was revoked", and a screen must
      // not show the second when the first happened.
      return refuse("sign in before revoking a link", 401);
    }

    const pool = sessionPool();
    // TWO DELETES IN ONE TRANSACTION. Separately, a failure between them leaves the
    // token deleted and the sessions live — or worse, the sessions deleted and the
    // token standing, which reads as revoked and is not.
    const client = await pool.connect();
    try {
      await client.query("BEGIN");
      // Parameterised. `userId` comes from a verified session rather than a body, and
      // it is parameterised anyway: the rule does not have exceptions for values that
      // happen to be trustworthy today.
      const accounts = await client.query(
        'DELETE FROM accounts WHERE "userId" = $1',
        [userId],
      );
      const sessions = await client.query(
        'DELETE FROM sessions WHERE "userId" = $1',
        [userId],
      );
      await client.query("COMMIT");

      return respond({
        revoked: true,
        // COUNTS, so the answer is falsifiable. `revoked: true` with zero rows deleted
        // would mean the token was never there — worth knowing, and indistinguishable
        // without these.
        accounts_removed: accounts.rowCount ?? 0,
        sessions_removed: sessions.rowCount ?? 0,
        // THE HALF THIS ENDPOINT CANNOT DO, named in the response rather than in a
        // comment nobody reading the UI will see.
        note:
          "this application no longer holds a token for you and you have been " +
          "signed out. The authorisation still exists at GitHub until you remove " +
          "it at github.com/settings/connections/applications",
      });
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  } catch (error) {
    return unhandled(error);
  }
}
