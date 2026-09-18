/**
 * /account -- who is signed in, which tenant they resolved to, and the GitHub link.
 *
 * A SERVER COMPONENT holding no data. The panel below it is the client component,
 * because it fetches, holds a confirm step and owns three states. Splitting them
 * this way means the words on this page -- which are the part a person reads before
 * doing something irreversible -- ship as HTML rather than waiting on JavaScript.
 *
 * WHY THE TENANT IS NAMED HERE AND NOT ONLY IN THE PANEL. The panel shows the
 * value; this page says the value cannot be changed. That sentence belongs beside
 * the heading rather than inside the row it describes, because a reader who is
 * looking for a way to switch tenants stops looking here instead of hunting for a
 * control that does not exist.
 */

import type { Metadata } from "next";

import { AccountPanel } from "@/components/AccountPanel";
import { requireIdentity } from "@/lib/guard";

export const metadata: Metadata = {
  title: "Account · The Agent Org",
  description: "Who you are signed in as, and the workspace it acts in.",
};

/**
 * SIGNED OUT, SO NOTHING BELOW IS RENDERED. `requireIdentity` redirects rather
 * than returning null -- see `lib/guard.ts` for why the no-token and the
 * no-workspace cases get different destinations, and why neither can loop.
 */
export default async function AccountPage() {
  await requireIdentity();

  return (
    <>
      {/* NO STANDING EXPLANATION. This carried a paragraph about how the tenant
          is resolved and what removing the GitHub link would stop -- an account
          screen explaining its own authorisation model to the person whose
          account it is. The facts below are self-describing; anything that needed
          a paragraph to justify it has been removed rather than annotated. */}
      <p className="eyebrow">Account</p>
      <h1 className="display" style={{ marginBottom: "var(--gap-8)" }}>
        This account
      </h1>

      <AccountPanel />
    </>
  );
}
