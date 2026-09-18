/**
 * ACCOUNT — who you are here, and the one control that belongs on this screen.
 *
 * **THIS PAGE USED TO EXPLAIN ITS OWN ARCHITECTURE.** Under the tenant it printed:
 * *"Resolved by the server for this sign-in, and not editable here. No request this
 * app sends carries a tenant, so a caller cannot name one."* True, load-bearing,
 * and written for somebody auditing the design rather than for the person reading
 * their own account. Nobody looking at their workspace name needs to be told which
 * request field does not exist. The rule it describes is enforced in `authz.ts` and
 * argued in `session.ts`, where a reader can act on it.
 *
 * **THE "GITHUB LINK" SECTION IS DELETED, AND IT HAD STOPPED BEING TRUE.** It
 * showed LINKED / NOT LINKED with a "Remove link" button and a confirmation step,
 * from the era when a GitHub token hung off a database account row that a person
 * could drop while keeping their login. GitHub IS the login now — there is nothing
 * to unlink that is not simply signing out, and the `DELETE /api/link/github` the
 * button called still reaches for Postgres, which this deployment does not have.
 * A control whose only outcomes are "no change" or "an error" is worse than none.
 *
 * What replaces it is the one sentence that is still actionable: this application
 * cannot revoke GitHub's own grant, and the place that can is named.
 *
 * Four facts, one control. Nothing here is a paragraph.
 */

"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";

import { getJson } from "@/components/fetching";
import { EmptyState, Skeleton } from "@/components/primitives";
import type { SessionView } from "@/lib/endpoints";

/** One labelled fact. The label is mono because it names a field, not a sentence. */
function Fact({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "grid",
        gap: "var(--gap-1)",
        paddingBlock: "var(--gap-3)",
        borderTop: "1px solid var(--border)",
      }}
    >
      <span className="eyebrow">{label}</span>
      <div style={{ fontSize: "var(--step-body)" }}>{children}</div>
    </div>
  );
}

export function AccountPanel() {
  const [session, setSession] = useState<SessionView | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      const result = await getJson<SessionView>("/api/session");
      if (result.ok) setSession(result.value);
      setLoading(false);
    })();
  }, []);

  if (loading) return <Skeleton label="Loading this account" rows={3} />;

  if (session === null || !session.signed_in) {
    return (
      <EmptyState
        headline="Nobody is signed in"
        action="Sign in with GitHub to see this account."
      >
        <Link href="/signin" className="btn" style={{ display: "inline-block" }}>
          Go to sign in
        </Link>
      </EmptyState>
    );
  }

  return (
    <div style={{ display: "grid", gap: "var(--gap-6)", maxWidth: "var(--measure)" }}>
      <div style={{ display: "flex", gap: "var(--gap-4)", alignItems: "center", flexWrap: "wrap" }}>
        {session.image ? (
          // `alt=""` on purpose: the login is beside it, so a description would be
          // read out twice. `unoptimized` because the avatar host is not in
          // `next.config.mjs`'s image config.
          <Image
            src={session.image}
            alt=""
            width={44}
            height={44}
            unoptimized
            style={{ borderRadius: "50%", border: "1px solid var(--border-strong)" }}
          />
        ) : null}
        <p className="title" style={{ margin: 0 }}>
          {session.login ?? "Unknown login"}
        </p>
      </div>

      <div>
        <Fact label="Signed in with">GitHub</Fact>

        <Fact label="Workspace">
          {/* `ident` is the mono treatment for a value the system generated. */}
          <span className="ident">{session.tenant_id ?? "none"}</span>
        </Fact>

        <Fact label="Repositories">
          {/* ACTIONABLE, where the removed paragraph was not. What a person wants
              from this row is the screen that changes it. */}
          <Link href="/repositories">Choose which repositories runs can act on</Link>
        </Fact>

        <Fact label="Ending access">
          {/* THE HONEST LIMIT, in one line and only because it is still actionable.
              Signing out ends the session here; GitHub's own authorisation
              survives until it is removed at GitHub, and this application cannot
              do that for you -- revoking a grant needs the client secret, which
              must not be reachable from a browser session on the process that can
              approve a gate. */}
          <a href="/api/auth/logout">Sign out</a>
          <span style={{ color: "var(--text-muted)", fontSize: "var(--step-small)" }}>
            {" "}
            · to withdraw the app&apos;s access entirely, remove it in{" "}
            <a
              href="https://github.com/settings/applications"
              target="_blank"
              rel="noreferrer"
            >
              your GitHub settings
            </a>
          </span>
        </Fact>
      </div>
    </div>
  );
}
