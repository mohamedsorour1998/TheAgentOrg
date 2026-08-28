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

export const metadata: Metadata = {
  title: "Account · The Agent Org",
  description:
    "The signed-in account, the tenant the server resolved for it, and the " +
    "GitHub link.",
};

export default function AccountPage() {
  return (
    <>
      <p className="eyebrow">Account</p>
      <h1 className="display" style={{ marginBottom: "var(--gap-4)" }}>
        This account
      </h1>
      <p className="prose" style={{ margin: "0 0 var(--gap-8)" }}>
        The tenant below is the one the server resolved for this sign-in. It is
        shown, not chosen: no request this app sends carries a tenant, so there
        is nothing here to change it with. A run acts on repositories through
        the GitHub link, so removing the link stops every future run.
      </p>

      <AccountPanel />
    </>
  );
}
