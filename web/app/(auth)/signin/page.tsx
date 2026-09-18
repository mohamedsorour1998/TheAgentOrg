/**
 * `/signin` — the front door.
 *
 * **THE "WHAT IT DOES NOT GRANT" PANEL IS GONE**, and its removal is the point
 * rather than a tidy-up. It read: *"No account here can override a security block.
 * That verdict comes from five lines of Python with no model in it, and overriding
 * it requires shell access rather than a click."* Every word true, and the operator
 * had already cut the same sentence once from `StartRun` as confusing. It is an
 * argument aimed at a judge, placed in front of somebody trying to get in — and a
 * sign-in screen is the worst possible place to explain an architectural boundary
 * to a reader who has not seen the product yet. It belongs in the docs and on the
 * deck, both of which carry it.
 *
 * What replaces it is one sentence about the only thing signing in changes for the
 * person doing it: their name goes on the decision.
 *
 * **ONE SATURATED COLOUR, SPENT ONCE** — the reference deployment's rule, quoted in
 * the layout. Cyan is the GitHub button and the wordmark's full stop. Nothing else
 * on this page is coloured, so the eye lands on the only control that matters.
 *
 * **GITHUB IS FIRST AND EMAIL IS A LINK**, because this product acts on GitHub
 * repositories and the account that owns them is the one whose name belongs beside
 * a gate decision. The email path is kept because accounts already exist on it —
 * `reviewer-01` among them — and removing the only way those accounts sign in is a
 * migration, not a redesign.
 *
 * A PLAIN `<a>` FOR BOTH, not `<Link>`: each leaves this origin (github.com, and
 * the Cognito domain). A client-side navigation cannot follow a cross-origin
 * redirect, so the click would end nowhere while looking fine.
 */

import Link from "next/link";

export const metadata = {
  title: "Sign in · The Agent Org",
  description: "Sign in with GitHub to record a gate decision under your own name.",
};

/**
 * Why a person landed back here, in their words rather than a code.
 *
 * Every one of these is reachable: `guard.ts` sends an authenticated account with
 * no workspace, and `/api/auth/github/callback` sends four distinct refusals. A
 * redirect that drops somebody on a blank sign-in form with no explanation is the
 * failure this map exists to prevent — they try the same button again and get the
 * same silence.
 */
const NOTICES: Record<string, string> = {
  unassigned:
    "You are signed in, and this account has no workspace yet. An administrator has to assign one before there is anything to see.",
  github_denied: "GitHub did not grant access. Nothing was changed.",
  github_state:
    "That sign-in took too long, or was started in another tab. Start it again from this page.",
  github_exchange: "GitHub refused the sign-in. Start it again from this page.",
  github_session: "Signing in did not complete. Try once more.",
  github: "GitHub sign-in is not available right now.",
};

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; account?: string }>;
}) {
  const params = await searchParams;
  const notice = NOTICES[params.account ?? ""] ?? NOTICES[params.error ?? ""] ?? "";

  return (
    <>
      <div style={{ display: "grid", gap: "var(--gap-3)" }}>
        {/* MONO, because it is the product's own name -- the reference
            deployment's distinction: mono for what the system wrote, sans for
            sentences a person reads. */}
        <p className="wordmark" style={{ fontSize: "var(--step-title)" }}>
          The Agent Org<span>.</span>
        </p>
        <h1 className="display" style={{ margin: 0, fontSize: "var(--step-title)" }}>
          Sign in
        </h1>
      </div>

      {notice ? (
        <p
          role="status"
          className="prose"
          style={{
            margin: 0,
            fontSize: "var(--step-small)",
            borderLeft: "3px solid var(--border-strong)",
            paddingLeft: "var(--gap-3)",
          }}
        >
          {notice}
        </p>
      ) : null}

      <div style={{ display: "grid", gap: "var(--gap-4)" }}>
        <a
          href="/api/auth/github"
          className="btn"
          style={{ display: "block", textAlign: "center", padding: "var(--gap-3)" }}
        >
          Continue with GitHub
        </a>

        <p
          className="prose"
          style={{ margin: 0, fontSize: "var(--step-small)", color: "var(--text-muted)" }}
        >
          Your GitHub login is written to the audit trail of every gate you approve
          or reject. This application never sees your password.
        </p>
      </div>

      {/* A HAIRLINE, NOT A CARD. The email route is an alternative, not a second
          offer of equal weight -- giving it a box would make the page ask a
          question it does not need to ask. */}
      <div
        style={{
          borderTop: "1px solid var(--border)",
          paddingTop: "var(--gap-4)",
          display: "grid",
          gap: "var(--gap-3)",
          fontSize: "var(--step-small)",
        }}
      >
        <p style={{ margin: 0 }}>
          <a href="/api/auth/signin">Sign in with an email address</a>
        </p>
        <p style={{ margin: 0, color: "var(--text-muted)" }}>
          New here? <Link href="/signup">Create an account</Link>
        </p>
      </div>
    </>
  );
}
