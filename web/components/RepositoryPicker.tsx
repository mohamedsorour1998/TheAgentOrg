/**
 * THE REPOSITORY PICKER. A checkbox per repository, and one "Save scope" button.
 *
 * WHY ONE SAVE AND NOT PER-ROW TOGGLES. `PUT /api/repositories` REPLACES the whole
 * set. Per-row requests over a replacing endpoint means every click sends the entire
 * set as the client last understood it, so two rapid clicks race and the loser's
 * repository silently leaves scope -- and leaving scope is the direction that stops
 * runs. One button over the current checkbox state sends one set, once.
 *
 * THE SERVER'S ANSWER IS THE TRUTH. After a save the list is re-seeded from the
 * PUT's own response, never from what was ticked. `RepositoryScopeRequest` says
 * "every one must be a repository the session's GitHub grant sees", so the server
 * may legitimately return a set that differs from the one asked for -- and a screen
 * that kept its local state would show the request as though it were the outcome.
 * That is this repository's signature defect: a check that cannot tell "did not run"
 * from "passed".
 *
 * UNSAVED STATE IS A SET COMPARISON, not a dirty flag. A flag set on first click
 * stays set after the ticks are put back the way they were, so the button offers to
 * save nothing; comparing against the last server answer means untick-then-retick
 * correctly disables it again.
 */

"use client";

import { useCallback, useEffect, useState } from "react";

import { getJson, sendJson } from "@/components/fetching";
import { ErrorState, Skeleton } from "@/components/primitives";
import type { RepositoryListResponse, RepositoryView } from "@/lib/endpoints";

type Failure = { error: string; fix: string; detail?: string };

/** The in-scope names of a list, as a set. The one shape both sides compare on. */
function scopeOf(repositories: readonly RepositoryView[]): Set<string> {
  return new Set(repositories.filter((r) => r.in_scope).map((r) => r.full_name));
}

/** Same members, ignoring order. `PUT` takes an array; scope is a set. */
function sameScope(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  if (a.size !== b.size) return false;
  for (const name of a) if (!b.has(name)) return false;
  return true;
}

export function RepositoryPicker() {
  /** What the server last returned. The baseline for "unsaved". */
  const [server, setServer] = useState<RepositoryView[] | null>(null);
  /** What the boxes say now. */
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [failure, setFailure] = useState<Failure | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  /**
   * THE REPOSITORIES THIS PERSON CAN PICK, from their GitHub App installation.
   *
   * Reported: *"i need dropdown i dont want to write any"*. Typing `owner/name`
   * means knowing the exact spelling, leaving the product to check it, and getting
   * a run scoped to a repository that does not exist if one character is wrong.
   *
   * **THREE STATES, NOT TWO**, because they want three different remedies and a
   * screen that collapses them sends people to the wrong one:
   *
   *     null                     still loading
   *     linked === false         signed in with email -- sign in with GitHub
   *     [] with linked === true  the app is installed nowhere -- install it
   *     unavailable === true     GitHub did not answer -- reload
   */
  const [available, setAvailable] = useState<string[] | null>(null);
  const [linked, setLinked] = useState<boolean | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [picked, setPicked] = useState("");

  /** Seed BOTH from one answer, so the baseline and the boxes cannot drift. */
  const adopt = useCallback((repositories: RepositoryView[]) => {
    setServer(repositories);
    setTicked(scopeOf(repositories));
  }, []);

  const load = useCallback(async () => {
    const result = await getJson<RepositoryListResponse>("/api/repositories");
    if (result.ok) {
      adopt(result.value.repositories);
      setFailure(null);
      return;
    }
    setFailure({ error: result.error, fix: result.fix, detail: result.detail });
  }, [adopt]);

  // AWAITED INSIDE AN ASYNC IIFE, not `void load()`. The preset's
  // `react-hooks/set-state-in-effect` refuses a setState reachable from an
  // effect's SYNCHRONOUS body, and it cannot see through `useCallback` to know
  // that `load`'s first statement is already an await. Awaiting here puts every
  // setState after a microtask boundary, which satisfies the rule by actually
  // moving the calls rather than by hiding them from the linter.
  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  /**
   * The pickable list, loaded once and INDEPENDENTLY of the scope above.
   *
   * Separate from `load()` on purpose: this one reaches GitHub, so it is slower and
   * it can fail on its own. Folding it into the scope read would make a GitHub
   * outage empty the screen that shows what is already in scope — a convenience
   * taking a working screen down with it.
   *
   * The async IIFE is not decoration: Next 16's `react-hooks/set-state-in-effect`
   * refuses a loader called from an effect body and cannot see through
   * `useCallback`, so every `setState` has to cross a microtask boundary.
   */
  useEffect(() => {
    void (async () => {
      const result = await getJson<{
        repositories: string[];
        linked: boolean;
        unavailable?: boolean;
      }>("/api/github/repositories");
      if (result.ok) {
        setAvailable(result.value.repositories);
        setLinked(result.value.linked);
        setUnavailable(result.value.unavailable === true);
        return;
      }
      // A refusal here is not worth a red banner: the scope list above still
      // renders, and this is the only thing that cannot be shown.
      setAvailable([]);
      setLinked(false);
    })();
  }, []);

  function toggle(fullName: string) {
    setSaved(false);
    setTicked((current) => {
      const next = new Set(current);
      if (next.has(fullName)) next.delete(fullName);
      else next.add(fullName);
      return next;
    });
  }

  async function saveScope() {
    setSaving(true);
    setSaved(false);
    const result = await sendJson<RepositoryListResponse>("PUT", "/api/repositories", {
      full_names: [...ticked],
    });
    setSaving(false);
    if (!result.ok) {
      setFailure({ error: result.error, fix: result.fix, detail: result.detail });
      return;
    }
    // Re-seed from the response, not from `ticked`.
    adopt(result.value.repositories);
    setFailure(null);
    setSaved(true);
  }

  /**
   * Stage a repository locally; `Save scope` is still the one write.
   *
   * **THE `owner/name` VALIDATION IS GONE WITH THE TEXT FIELD.** It existed to
   * catch a typo, and the only caller now is the dropdown, whose values come from
   * GitHub's own API -- so the shape cannot be wrong, and a branch that can never
   * be taken is a branch nobody will ever see fail. `PUT /api/repositories`
   * revalidates every entry server-side regardless, which is the check that
   * matters.
   */
  function addName(name: string) {
    // TICK IT RATHER THAN RE-ADDING. A repository that is listed-but-unticked is
    // one somebody removed from scope and is now putting back; appending a second
    // row for it would render the same repository twice.
    if (!server?.some((r) => r.full_name === name)) {
      setServer((was) => [...(was ?? []), { full_name: name, in_scope: false }]);
    }
    setTicked((was) => new Set(was).add(name));
    setSaved(false);
  }

  if (failure && server === null) {
    return <ErrorState error={failure.error} fix={failure.fix} detail={failure.detail} />;
  }
  if (server === null) return <Skeleton label="Loading repositories" rows={5} />;


  const unsaved = !sameScope(ticked, scopeOf(server));

  return (
    <div style={{ display: "grid", gap: "var(--gap-6)", maxWidth: "var(--measure)" }}>
      {failure ? (
        <ErrorState error={failure.error} fix={failure.fix} detail={failure.detail} />
      ) : null}

      {/* PICK, DO NOT TYPE. The list is what the GitHub App was INSTALLED on --
          not everything the account can see, which is what an OAuth App's `repo`
          scope would have given. Already-listed repositories are filtered out, so
          the dropdown only ever offers something that would actually change the
          scope; an option that does nothing when chosen reads as a broken control. */}
      {available === null ? null : (() => {
        const listed = (name: string) => server.some((r) => r.full_name === name);
        /**
         * **SHOWN EVEN WHEN EVERYTHING IS ALREADY IN SCOPE, and the first version
         * was not.** It filtered added repositories out of the options, so with one
         * installed repository already listed there was nothing to offer and the
         * control vanished, replaced by a sentence. Reported: *"where is the damn
         * dropmenu"* — with the explanation sitting right above it, unread, because
         * a person looking for a dropdown scans for a dropdown.
         *
         * A control that disappears when it has nothing to say is indistinguishable
         * from one that is broken. The already-added entries stay, DISABLED and
         * marked, so the list still answers "what can I pick?" and shows why each
         * one is not pickable.
         */
        if (available.length > 0) {
          const offerable = available.filter((name) => !listed(name));
          return (
            <div style={{ display: "grid", gap: "var(--gap-2)" }}>
              <label htmlFor="pick-repository" className="eyebrow" style={{ margin: 0 }}>
                Pick a repository
              </label>
              <div style={{ display: "flex", gap: "var(--gap-3)", flexWrap: "wrap" }}>
                <select
                  id="pick-repository"
                  value={picked}
                  onChange={(event) => setPicked(event.target.value)}
                  style={{
                    padding: "var(--gap-2)",
                    font: "inherit",
                    minWidth: "22ch",
                    flex: "1 1 22ch",
                    background: "var(--surface-raised)",
                    color: "var(--text)",
                    border: "1px solid var(--border)",
                  }}
                >
                  <option value="">
                    {offerable.length > 0
                      ? "Choose from your GitHub installation…"
                      : "All of your installed repositories are already in scope"}
                  </option>
                  {available.map((name) => (
                    <option key={name} value={name} disabled={listed(name)}>
                      {name}
                      {listed(name) ? "  — already in scope" : ""}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn"
                  disabled={!picked}
                  onClick={() => {
                    addName(picked);
                    setPicked("");
                  }}
                >
                  Add
                </button>
              </div>
            </div>
          );
        }
        // THE THREE EMPTIES, EACH WITH ITS OWN REMEDY. Collapsing them tells
        // somebody to install an app they have already installed, or to sign in
        // when they already are.
        const note = unavailable
          ? "GitHub did not answer, so there is no list to choose from. Reload to try again."
          : linked === false
            ? "Sign in with GitHub to pick from a list instead of typing."
            : available.length === 0
              ? "The Agent Org app is not installed on any repository yet. Install it on GitHub, then reload."
              : "Every repository from your installation is already listed below.";
        return (
          <p className="prose" style={{ margin: 0, fontSize: "var(--step-small)", opacity: 0.8 }}>
            {note}
          </p>
        );
      })()}

      {/* THE TEXT FIELD IS GONE, AND THE LINK IS WHAT REPLACES IT.
          Reported: "i need only drop down and i dont want to enter repo by hand".
          It was kept beside the dropdown on the reasoning that somebody might add
          a repository before installing the App there -- but that is not an entry
          path, it is a trap: a name typed here reaches the scope list and no run
          against it can ever open a pull request or post a comment, because the
          App holds no installation on it. **A control that accepts a value the
          system cannot act on is worse than no control**, and the failure lands
          later, on a run, where nothing connects it back to this screen.

          So the only way in is the dropdown, and the honest answer to "why is
          only one repository listed" is one click away rather than a paragraph.
          The list IS the installation -- that is the per-repository scope a
          GitHub App gives and an OAuth App cannot. */}
      <p
        className="prose"
        style={{ margin: 0, fontSize: "var(--step-small)", color: "var(--text-muted)" }}
      >
        This list is exactly the repositories you installed The Agent Org on.{" "}
        <a
          href="https://github.com/settings/installations"
          target="_blank"
          rel="noreferrer"
        >
          Add more on GitHub
        </a>
        , then reload.
      </p>

      <fieldset style={{ border: "1px solid var(--border)", borderRadius: "4px", padding: "var(--gap-4)", margin: 0 }}>
        <legend className="eyebrow" style={{ margin: 0, padding: "0 var(--gap-2)" }}>
          In scope
        </legend>
        {server.length === 0 ? (
          // NOT AN ERROR, AND NOT A DEAD END. A tenant that has added nothing yet
          // is the normal state of every account the moment it signs up -- and the
          // screen must say so while still offering the field above.
          <p className="prose" style={{ margin: 0, fontSize: "var(--step-small)" }}>
            Nothing is in scope yet, so no run can be started. Add a repository
            above.
          </p>
        ) : null}
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: "var(--gap-1)" }}>
          {server.map((repository) => {
            const id = `scope-${repository.full_name}`;
            return (
              <li key={repository.full_name}>
                {/* A REAL <label> WRAPPING THE INPUT, plus `htmlFor`. The wrap makes
                    the whole row a hit target on a phone; the id association is what
                    a screen reader reads. */}
                <label
                  htmlFor={id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--gap-3)",
                    padding: "var(--gap-3) var(--gap-2)",
                    minHeight: "44px",
                    cursor: "pointer",
                  }}
                >
                  <input
                    id={id}
                    type="checkbox"
                    checked={ticked.has(repository.full_name)}
                    onChange={() => toggle(repository.full_name)}
                    style={{ width: "1.1rem", height: "1.1rem", accentColor: "var(--accent)", flex: "none" }}
                  />
                  <span className="ident">{repository.full_name}</span>
                </label>
              </li>
            );
          })}
        </ul>
      </fieldset>

      <div style={{ display: "flex", alignItems: "center", gap: "var(--gap-4)", flexWrap: "wrap" }}>
        <button type="button" className="btn" onClick={() => void saveScope()} disabled={!unsaved || saving}>
          {saving ? "Saving scope" : "Save scope"}
        </button>

        {/* One live region for all three answers, so a screen reader hears the
            outcome without the page moving focus. The words match the button:
            "Save scope" -> "Scope saved". */}
        <p role="status" style={{ margin: 0, fontSize: "var(--step-small)" }}>
          {unsaved ? (
            <span style={{ color: "var(--accent)" }}>
              Unsaved: these boxes differ from what the server holds.
            </span>
          ) : saved ? (
            <span style={{ color: "var(--shipped)" }}>Scope saved.</span>
          ) : (
            <span style={{ color: "var(--text-muted)" }}>
              These boxes match what the server holds.
            </span>
          )}
        </p>
      </div>
    </div>
  );
}
