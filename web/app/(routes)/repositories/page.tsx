/**
 * /repositories -- which repositories this tenant may run against.
 *
 * A SERVER COMPONENT. The picker beneath it fetches and holds the checkbox state;
 * the consequence of putting a repository in scope is stated here, in HTML, because
 * it is the sentence a person needs BEFORE they tick a box rather than after.
 *
 * THE CONSEQUENCE IS NOT SOFTENED. Scope is not a filter on a list -- it is
 * permission for this pipeline to open pull requests and post comments on somebody
 * else's repository. `agentorg/integrations/` records that the offline path refuses
 * a repository it did not create precisely because an earlier version committed into
 * a victim's checkout; the same hazard read from the other end is a person ticking a
 * box without being told what it authorises.
 */

import type { Metadata } from "next";

import { RepositoryPicker } from "@/components/RepositoryPicker";

export const metadata: Metadata = {
  title: "Repositories · The Agent Org",
  description:
    "Choose which repositories this tenant can run against. A repository in " +
    "scope may receive pull requests and comments from this pipeline.",
};

export default function RepositoriesPage() {
  return (
    <>
      <p className="eyebrow">Repositories</p>
      <h1 className="display" style={{ marginBottom: "var(--gap-4)" }}>
        Repositories this tenant can run against
      </h1>
      <p className="prose" style={{ margin: "0 0 var(--gap-8)" }}>
        Putting a repository in scope lets this pipeline open pull requests and
        post comments on it. Runs can only start against a repository that is in
        scope, so this list is the whole of what the tenant can reach. Saving
        replaces the set, which means unticking a repository removes it.
      </p>

      <RepositoryPicker />
    </>
  );
}
