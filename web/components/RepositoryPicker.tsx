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
   * The `owner/name` being typed. THE LIST USED TO BE UNADDABLE, and that was a
   * real gap rather than a strict design: the checkboxes are built from what the
   * server already holds, so an account could UNTICK a repository and never add
   * one. It read as a picker and behaved as a remover.
   *
   * The original design assumed a CANDIDATE list -- every repository the linked
   * GitHub grant could see, ticked where in scope. That list no longer exists:
   * `github_linked` is permanently false because nothing writes an `accounts` row
   * any more, so `server` is just the in-scope set and the intersection of "what
   * you may add" with "what you already have" is everything you already have.
   */
  const [typed, setTyped] = useState("");
  const [typoed, setTypoed] = useState("");

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
   *     unavailable === true     GitHub did not answer -- type the name instead
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
      // A refusal here is not worth a red banner: the text field still works.
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
   * VALIDATED HERE ONLY TO SAY SO IMMEDIATELY. `PUT /api/repositories` revalidates
   * every entry server-side and is the check that matters -- this one exists so a
   * person learns about a typo while they are looking at the field, rather than
   * after a round trip.
   */
  /**
   * ONE PATH INTO THE LIST, whether the name was PICKED or TYPED.
   *
   * Extracted when the dropdown arrived. Two entry points each doing their own
   * de-duplication and ticking is how the picked path and the typed path end up
   * disagreeing about what "already listed" means -- and only one of them would
   * ever be exercised by hand.
   */
  function addName(raw: string) {
    const name = raw.trim();
    if (name.split("/").length !== 2 || name.split("/").some((part) => !part)) {
      setTypoed("Use the form owner/name, as GitHub writes it.");
      return;
    }
    if (server?.some((r) => r.full_name === name)) {
      // TICK IT RATHER THAN REFUSING. Somebody typing a name that is already
      // listed-but-unticked means to put it back in scope, and an error would
      // make them hunt for a row they cannot see the state of.
      setTicked((was) => new Set(was).add(name));
      setTyped("");
      setTypoed("");
      return;
    }
    setServer((was) => [...(was ?? []), { full_name: name, in_scope: false }]);
    setTicked((was) => new Set(was).add(name));
    setTyped("");
    setTypoed("");
  }

  function addTyped() {
    addName(typed);
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
        const offerable = available.filter((name) => !server.some((r) => r.full_name === name));
        if (offerable.length > 0) {
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
                  <option value="">Choose from your GitHub installation…</option>
                  {offerable.map((name) => (
                    <option key={name} value={name}>
                      {name}
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
          ? "GitHub did not answer, so there is no list to choose from. Type the name instead."
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

      {/* ADD A REPOSITORY. A form so Enter submits, which is how anyone types
          one name after another. `noValidate` is absent deliberately -- there is
          no `pattern` here, because the message this component writes is better
          than the browser's, and the server revalidates regardless.

          KEPT BESIDE THE DROPDOWN rather than replaced by it: the list covers only
          repositories the App is installed on, and somebody adding a repository
          before installing there would otherwise have no way in at all. */}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          addTyped();
        }}
        style={{ display: "grid", gap: "var(--gap-2)" }}
      >
        <label htmlFor="add-repository" className="eyebrow" style={{ margin: 0 }}>
          Add a repository
        </label>
        <div style={{ display: "flex", gap: "var(--gap-3)", flexWrap: "wrap" }}>
          <input
            id="add-repository"
            value={typed}
            onChange={(event) => {
              setTyped(event.target.value);
              setTypoed("");
            }}
            placeholder="owner/name"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            style={{ padding: "var(--gap-2)", font: "inherit", minWidth: "22ch", flex: "1 1 22ch" }}
          />
          <button type="submit" className="btn" disabled={!typed.trim()}>
            Add
          </button>
        </div>
        <p role="status" style={{ margin: 0, fontSize: "var(--step-small)" }}>
          {typoed ? (
            <span style={{ color: "var(--refused)" }}>{typoed}</span>
          ) : (
            <span style={{ opacity: 0.8 }}>
              Adding puts it in the list below, ticked. Nothing reaches the server
              until you save.
            </span>
          )}
        </p>
      </form>

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
