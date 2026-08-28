/**
 * SIGN IN — one action, because there is only one thing a person can do here.
 *
 * WHY THERE IS NO FORM ON THIS SCREEN
 * ===================================
 * Auth is Auth.js with GitHub OAuth (Lane I owns `/api/auth/[...nextauth]`), so
 * this app never receives a password. That removes two screens rather than
 * hiding them: there is no "sign up" — the first sign-in creates the account —
 * and there is no "reset password", because there is no password to reset. An
 * email/password form here could not work, and a form that cannot work is worse
 * than an honest absence: it reads as a capability, and the reader spends their
 * attempt discovering it is not one. Same argument as `endpoints.ts` makes about
 * a scope nobody holds.
 *
 * WHICH ENTRY POINT, AND HOW IT WAS VERIFIED
 * ==========================================
 * `signIn` from `next-auth/react`, checked rather than assumed — next-auth
 * 5.0.0-beta.32 is a beta and its export map is the only authority:
 *
 *     node -e "const p=require('next-auth/package.json'); console.log(p.exports)"
 *       -> "./react": { types: "./react.d.ts", import: "./react.js" }
 *     node --input-type=module -e "import('next-auth/react')
 *            .then(m => console.log(typeof m.signIn))"
 *       -> function
 *
 * So the plain-`<form method="post">` fallback is NOT needed. It would also not
 * have worked unaided: `react.js`'s `signIn` fetches `getCsrfToken()` and posts
 * `csrfToken` in the body, and Auth.js refuses a POST without it. A bare form
 * would need the token fetched first, which is JavaScript again.
 *
 * A CLIENT COMPONENT, for two reasons that cannot be moved to the server:
 * `signIn` navigates the browser, and `getJson` fetches a RELATIVE path, which
 * only resolves in a document. That is also why the session read happens here
 * rather than in the page.
 */

"use client";

import Link from "next/link";
import { signIn } from "next-auth/react";
import { useCallback, useEffect, useState } from "react";

import { getJson } from "@/components/fetching";
import { EmptyState, ErrorState, Skeleton } from "@/components/primitives";
import type { SessionView } from "@/lib/endpoints";

/** `ErrorState`'s three props, kept together so one slot holds any failure. */
interface Failure {
  error: string;
  fix: string;
  detail?: string;
}

export function SignInPanel() {
  const [session, setSession] = useState<SessionView | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);

  // Read who is signed in. Someone who already is must not be shown a button
  // that sends them round a round trip to arrive back here -- they get told they
  // are signed in, and a way on to the runs.
  useEffect(() => {
    let live = true;
    void (async () => {
      const result = await getJson<SessionView>("/api/session");
      if (!live) return;
      if (result.ok) {
        setSession(result.value);
      } else {
        // NAMES WHAT COULD NOT BE READ, then what happened. Rendering only
        // `result.error` would leave a reader unable to tell whether the app
        // failed to check or checked and found them signed out -- and those want
        // different actions.
        setFailure({
          error: `Whether you are signed in could not be read. ${result.error}`,
          fix: result.fix,
          detail: result.detail,
        });
      }
      setLoading(false);
    })();
    return () => {
      live = false;
    };
  }, []);

  const start = useCallback(async () => {
    setStarting(true);
    setFailure(null);
    try {
      // On success this navigates to GitHub, so `starting` is deliberately not
      // reset: the button stays disabled while the page is on its way out.
      await signIn("github", { redirectTo: "/runs" });
    } catch (cause) {
      setStarting(false);
      setFailure({
        // The button says "Continue with GitHub", so the failure uses the same
        // words. A message naming an "authentication provider" would leave the
        // reader matching it to something they clicked.
        error: "Continuing with GitHub did not start.",
        fix: "Retry once. If it repeats, this needs an operator rather than another click.",
        detail:
          cause instanceof Error ? `${cause.name}: ${cause.message}` : String(cause),
      });
    }
  }, []);

  if (loading) {
    return <Skeleton label="Checking whether you are signed in" rows={2} />;
  }

  const signedIn = session?.signed_in === true;

  return (
    <div style={{ display: "grid", gap: "var(--gap-6)" }}>
      {failure ? <ErrorState {...failure} /> : null}

      {signedIn && session ? (
        <div className="card" style={{ maxWidth: "var(--measure)" }}>
          <p className="eyebrow">Signed in</p>
          <p className="title" style={{ marginBottom: "var(--gap-4)" }}>
            You are signed in as <span className="ident">{session.login}</span>
          </p>
          <dl
            style={{
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              gap: "var(--gap-2) var(--gap-4)",
              margin: `0 0 var(--gap-6)`,
              fontSize: "var(--step-small)",
            }}
          >
            <dt className="eyebrow" style={{ margin: 0 }}>
              Name
            </dt>
            <dd style={{ margin: 0 }}>{session.name ?? "not recorded"}</dd>
            <dt className="eyebrow" style={{ margin: 0 }}>
              Tenant
            </dt>
            <dd className="ident" style={{ margin: 0 }}>
              {session.tenant_id ?? "not resolved"}
            </dd>
          </dl>
          {session.github_linked ? null : (
            // Signed in with the GitHub grant revoked is a real state -- there
            // is a route that revokes it -- and it is not a fault. Stated, with
            // the same button as the remedy, so the word does not change.
            <p
              className="prose"
              style={{ margin: `0 0 var(--gap-4)`, fontSize: "var(--step-small)" }}
            >
              This account has no GitHub grant linked, so no repository is
              readable yet. Continuing with GitHub again restores it.
            </p>
          )}
          {session.github_linked ? (
            <Link href="/runs">Go to the runs</Link>
          ) : (
            <button
              type="button"
              className="btn"
              onClick={() => void start()}
              disabled={starting}
            >
              {starting ? "Continuing with GitHub…" : "Continue with GitHub"}
            </button>
          )}
        </div>
      ) : (
        <EmptyState
          headline="You are not signed in"
          action="Signing in records your GitHub login against every gate decision you make."
        >
          <button
            type="button"
            className="btn"
            onClick={() => void start()}
            disabled={starting}
          >
            {starting ? "Continuing with GitHub…" : "Continue with GitHub"}
          </button>
          <p
            className="prose"
            style={{ margin: `var(--gap-4) 0 0`, fontSize: "var(--step-small)" }}
          >
            GitHub is the only way in, and the first sign-in creates the account.
            There is no password here, so there is nothing to reset and no
            separate sign-up to find.
          </p>
        </EmptyState>
      )}
    </div>
  );
}
