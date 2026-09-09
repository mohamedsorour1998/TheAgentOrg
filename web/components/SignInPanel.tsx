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
 * WHICH ENTRY POINT, AND WHY IT IS A NAVIGATION RATHER THAN A CALL
 * ================================================================
 * `window.location.assign("/api/auth/signin")`. Lane P replaced Auth.js with
 * Cognito, and the shape of the handoff changed with it: this used to call
 * `signIn` from `next-auth/react`, which fetched a CSRF token and POSTed it.
 *
 * A CLIENT-SIDE HELPER CANNOT DO WHAT THE COGNITO FLOW NEEDS. `GET
 * /api/auth/signin` mints a `state` value, sets it as an `HttpOnly` cookie, and
 * 307s to the hosted UI; the callback then compares the cookie against the
 * `state` parameter Cognito hands back. `HttpOnly` is the point — script cannot
 * read or write it, which is what makes the comparison worth doing — so the
 * cookie must be set by the server that will later check it. A `fetch` would
 * get the redirect as a response rather than following it as a navigation.
 *
 * THIS FILE WAS THE LAST next-auth IMPORT IN THE APPLICATION. Lane P could not
 * remove the package while it stood, because `components/` was outside its
 * ownership and deleting the dependency would have turned `tsc` red on a file it
 * could not edit. Ported by the integrator at merge.
 *
 * STILL A CLIENT COMPONENT, for one reason that survives the change: `getJson`
 * fetches a RELATIVE path, which only resolves in a document. That is why the
 * session read happens here rather than in the page.
 */

"use client";

import Link from "next/link";
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

  /** Mark the button busy. THE FORM NAVIGATES; this handler does not.
   *
   * `<form action="/api/auth/signin" method="get">` is the whole mechanism, and it
   * is a real navigation rather than a call. `GET /api/auth/signin` mints a
   * `state` value, sets it `HttpOnly`, and 307s to the hosted UI — `HttpOnly` is
   * the point, since script can neither read nor write it, which is what makes the
   * callback's comparison worth doing. A `fetch` would receive the redirect as a
   * response instead of following it.
   *
   * It also works with JavaScript disabled, which `window.location.assign` did
   * not — and eslint refuses that call for internal paths anyway
   * (`no-location-assign-relative-destination`). The rule reads a relative
   * destination as an internal page; this one is a route handler that leaves the
   * origin. A form satisfies both the rule and the reasoning behind it rather than
   * suppressing either.
   *
   * NO try/catch AND NO FAILURE STATE, because there is no call to fail. A
   * navigation that cannot be made is the browser's error page, not this
   * component's — and a `failure` slot that could never be filled would be a
   * refusal nobody can reach.
   */
  const onStart = useCallback(() => {
    setStarting(true);
    setFailure(null);
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
          {session.tenant_id ? null : (
            // SIGNED IN AND NOT YET AUTHORISED IS A REAL STATE, and after the
            // Cognito migration it is the NORMAL state of every new account:
            // self-signup is allowed, and `custom:tenant` is set by an
            // administrator afterwards. The predicate is `tenant_id === null`
            // rather than `github_linked`, which is now permanently false because
            // nothing writes an `accounts` row any more.
            //
            // SAYING IT IS THE POINT. The alternative is a run list that is
            // empty, which reads as "nothing has happened" when the truth is "you
            // may not see what has". That is the did-not-run-versus-passed
            // conflation this repository exists to refuse, rendered on a screen —
            // the same reason Lane J gave provenance three visual axes instead of
            // a colour.
            <p
              className="prose"
              style={{ margin: `0 0 var(--gap-4)`, fontSize: "var(--step-small)" }}
            >
              This account is not assigned to an organisation yet, so there are
              no runs it may read. That is not an empty list — it is a permission
              nobody has granted. An administrator assigns it.
            </p>
          )}
          {session.tenant_id ? (
            <Link href="/runs">Go to the runs</Link>
          ) : null}
        </div>
      ) : (
        <EmptyState
          headline="You are not signed in"
          action="Signing in records your GitHub login against every gate decision you make."
        >
          <form action="/api/auth/signin" method="get" onSubmit={onStart}>
            <button type="submit" className="btn" disabled={starting}>
              {starting ? "Taking you to sign-in…" : "Sign in"}
            </button>
          </form>
          <p
            className="prose"
            style={{ margin: `var(--gap-4) 0 0`, fontSize: "var(--step-small)" }}
          >
            Sign in or create an account through the hosted sign-in page. A new
            account can see nothing until somebody assigns it to an organisation
            — you will be told so plainly rather than shown an empty list.
          </p>
        </EmptyState>
      )}
    </div>
  );
}
