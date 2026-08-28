/**
 * `/signin` — the screen that says what signing in GRANTS.
 *
 * A SERVER COMPONENT holding only prose, with the one interactive part in
 * `SignInPanel`. Nothing on this page needs state, so nothing above the panel
 * ships JavaScript.
 *
 * WHY THE COPY NAMES THE GATE DECISION EXPLICITLY
 * ===============================================
 * This application approves security gates, and a person clicking "approved"
 * here is recorded by name -- `endpoints.ts` refuses a `by` field in the request
 * body precisely so the server attributes the decision to the session rather
 * than to whatever the client claimed. A sign-in screen that said only "sign in
 * to continue" would be the last moment before that, saying nothing about it.
 * `approve_server.py` records every decision as `"ui-reviewer"` because it has no
 * authentication and "genuinely does not know who clicked"; this surface does
 * know, and that is the whole reason it exists, so it says so.
 *
 * The claim is deliberately two sentences and not a policy. A policy is not read.
 */

import { SignInPanel } from "@/components/SignInPanel";

export const metadata = {
  title: "Sign in · The Agent Org",
  description:
    "Sign in with GitHub to record a gate decision under your own name.",
};

export default function SignInPage() {
  return (
    <div style={{ display: "grid", gap: "var(--gap-8)" }}>
      <div>
        <p className="eyebrow">Sign in</p>
        <h1 className="display">Sign in with GitHub</h1>
      </div>

      <p className="prose" style={{ margin: 0 }}>
        This application records decisions on security gates. Signing in gives you
        one capability: to approve or reject a gate under your own name, which is
        written to the run&apos;s audit trail and cannot be attributed to anyone
        else.
      </p>

      <SignInPanel />

      {/* WHAT SIGNING IN DOES NOT GRANT. Kept on the screen rather than in a
          help page, because the honest limit is short and a judge reads it here:
          a network route deliberately cannot override a security block, and the
          documented route for that stays a shell command. */}
      <div
        className="card"
        style={{ maxWidth: "var(--measure)", background: "var(--surface-sunken)" }}
      >
        <p className="eyebrow">What it does not grant</p>
        <p
          style={{
            margin: 0,
            color: "var(--text-muted)",
            fontSize: "var(--step-small)",
          }}
        >
          No account here can override a security block. That verdict comes from
          five lines of Python with no model in it, and overriding it requires
          shell access rather than a click.
        </p>
      </div>
    </div>
  );
}
