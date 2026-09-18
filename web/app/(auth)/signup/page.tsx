/**
 * `/signup` — register, as its own page.
 *
 * **IT USED TO BE A PANEL AT THE BOTTOM OF THE SIGN-IN SCREEN.** One page asked
 * two different questions of two different people — a returning reviewer and
 * somebody who has never been here — and answered neither cleanly: the returning
 * reviewer scrolled past a password field they must not use, and the new person
 * had to work out that the form below the fold was for them. Reported as wanting
 * a normal flow: a login page, and a register page, with a link between them.
 *
 * **EVERY ACCOUNT CREATED HERE GETS ITS OWN WORKSPACE, EMPTY.** That is the honest
 * answer and it is worth saying on the page rather than discovering: handing a new
 * sign-up the original deployment's runs would be the isolation failing quietly, so
 * an empty screen is the demonstration rather than a disappointment. The copy says
 * so in one line, in the person's terms — "starts empty" rather than "a fresh
 * tenant is provisioned".
 *
 * The form is `SignUpForm`, unchanged in behaviour: email, password, then the code
 * Cognito emails. It no longer draws its own card or heading, because this page
 * supplies both.
 */

import Link from "next/link";

import { SignUpForm } from "@/components/SignUpForm";

export const metadata = {
  title: "Create an account · The Agent Org",
  description: "Create an account to approve or reject security gates under your own name.",
};

export default function SignUpPage() {
  return (
    <>
      <div style={{ display: "grid", gap: "var(--gap-3)" }}>
        <p className="wordmark" style={{ fontSize: "var(--step-title)" }}>
          The Agent Org<span>.</span>
        </p>
        <h1 className="display" style={{ margin: 0, fontSize: "var(--step-title)" }}>
          Create an account
        </h1>
        <p
          className="prose"
          style={{ margin: 0, fontSize: "var(--step-small)", color: "var(--text-muted)" }}
        >
          Your account gets its own workspace, so it starts empty rather than
          showing somebody else&apos;s runs.
        </p>
      </div>

      <SignUpForm />

      <div
        style={{
          borderTop: "1px solid var(--border)",
          paddingTop: "var(--gap-4)",
          fontSize: "var(--step-small)",
          color: "var(--text-muted)",
        }}
      >
        {/* THE WAY BACK. A register page with no link to sign in strands anybody
            who arrived here by mistake, and "a page nothing links to is a page
            nobody can use" cuts both ways -- so does a page nothing links FROM. */}
        <p style={{ margin: 0 }}>
          Already have an account? <Link href="/signin">Sign in</Link>
        </p>
      </div>
    </>
  );
}
