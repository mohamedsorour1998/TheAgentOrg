/**
 * `/signin` — one door.
 *
 * **GITHUB IS THE ONLY WAY IN, AND SIGNING UP IS THE SAME ACT AS SIGNING IN.**
 * There is no register page and nothing is missing: `tenantForGitHub` derives a
 * workspace from the GitHub account id, so a person who has never been here gets
 * one on their first "Continue with GitHub". A separate registration form would be
 * asking for facts we already have from GitHub, to create an account that already
 * exists the moment they authorise.
 *
 * That also removes the thing the two-account model could not answer honestly: an
 * email account and a GitHub account were two identities for one human, with two
 * workspaces and two different names on the same person's gate decisions. One
 * provider, one identity, one name in the audit trail.
 *
 * **THE EMAIL PATH IS GONE FROM THE UI.** `/api/auth/signin` and its callback still
 * exist and still work if typed — `currentIdentity()` still verifies a Cognito
 * session, so `reviewer-01` is not locked out — but nothing links to them any more.
 * That is stated rather than implied, because unreferenced routes are this
 * repository's second named pattern and somebody should delete them deliberately
 * rather than discover them.
 *
 * A PLAIN `<a>`, not `<Link>`: this leaves our origin for github.com, and a
 * client-side navigation cannot follow a cross-origin redirect, so the click would
 * end nowhere while looking fine.
 */

export const metadata = {
  title: "Sign in · The Agent Org",
  description: "Sign in with GitHub to approve or reject security gates under your own name.",
};

/**
 * Why a person landed back here, in their words rather than a code.
 *
 * Every one is reachable: `guard.ts` sends an authenticated account with no
 * workspace, and the GitHub callback sends four distinct refusals. A redirect that
 * drops somebody on a blank form with no explanation is the failure this map
 * prevents — they press the same button again and get the same silence.
 */
const NOTICES: Record<string, string> = {
  unassigned:
    "You are signed in, and this account has no workspace yet. An administrator has to assign one.",
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
        {/* MONO, because it is the product's own name. The reference deployment's
            distinction: mono for what the system wrote, sans for sentences a
            person reads. */}
        <p className="wordmark" style={{ fontSize: "var(--step-title)" }}>
          The Agent Org<span>.</span>
        </p>
        <p className="prose" style={{ margin: 0, color: "var(--text-muted)" }}>
          Five agents write the change. Three gates need a person. You are the person.
        </p>
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

        {/* ONE SENTENCE, and it is about the person rather than the architecture.
            A panel here used to explain what an account cannot override; that is
            an argument for a judge, aimed at somebody trying to get in. */}
        <p
          className="prose"
          style={{ margin: 0, fontSize: "var(--step-small)", color: "var(--text-muted)" }}
        >
          First time here works the same way — your GitHub account is the account.
          Your login is written to the audit trail of every gate you decide, and this
          application never sees your password.
        </p>
      </div>
    </>
  );
}
