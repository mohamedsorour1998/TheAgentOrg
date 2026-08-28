/**
 * THE ACCOUNT PANEL. Who is signed in, which tenant, and the GitHub link.
 *
 * A client component, because it fetches and because removing the link is a
 * two-step interaction. Three states, all rendered: `Skeleton`, `ErrorState`,
 * `EmptyState`.
 *
 * THE CONFIRM IS A SECOND CLICK IN THE PAGE, NOT `window.confirm`. A native
 * confirm dialog cannot say what removing the link does -- it gets one line, no
 * mono for the identifier, and no way to name the reversal -- and it is dismissed
 * by the same reflex that opened it. Removing the link revokes the grant, so no
 * further run can act on any repository; that sentence has to be on screen at the
 * moment of the decision, which means it has to be our markup.
 *
 * THE LINK MARK IS DECLARED HERE and that is deliberate rather than lazy.
 * `components/vocabulary.ts` is the ONE table for provenance, run status and
 * verdict -- the three whose collapse makes a screen state something false. Link
 * state is not in it, and this lane does not own that file, so a local `Mark`
 * value is the honest option: it is a use of the shared type, not a second
 * declaration of a shared fact.
 */

"use client";

import Image from "next/image";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { getJson, sendJson } from "@/components/fetching";
import { EmptyState, ErrorState, Mark, Skeleton } from "@/components/primitives";
import type { Mark as MarkValue } from "@/components/vocabulary";
import type { SessionView } from "@/lib/endpoints";

/** Linked and not linked. Two states, and neither is ever a default. */
const LINK: Readonly<Record<"linked" | "absent", MarkValue>> = {
  linked: {
    label: "Linked",
    meaning: "A GitHub grant is in place, so runs can act on repositories.",
    tone: "shipped",
    form: "solid",
  },
  absent: {
    label: "Not linked",
    meaning:
      "No GitHub grant. Runs cannot open pull requests or post comments until " +
      "this account signs in again.",
    tone: "muted",
    form: "dashed",
  },
};

/** What the unlink control is doing right now. `confirm` is the second click. */
type Step = "idle" | "confirm" | "sending" | "done";

export function AccountPanel() {
  const [session, setSession] = useState<SessionView | null>(null);
  const [failure, setFailure] = useState<{
    error: string;
    fix: string;
    detail?: string;
  } | null>(null);
  const [step, setStep] = useState<Step>("idle");

  const load = useCallback(async () => {
    const result = await getJson<SessionView>("/api/session");
    if (result.ok) {
      setSession(result.value);
      setFailure(null);
      return;
    }
    setFailure({ error: result.error, fix: result.fix, detail: result.detail });
  }, []);

  // AWAITED INSIDE AN ASYNC IIFE, not `void load()`. The preset's
  // `react-hooks/set-state-in-effect` refuses a setState reachable from an
  // effect's SYNCHRONOUS body and cannot see through `useCallback`. Awaiting puts
  // every setState after a microtask boundary, which satisfies the rule by moving
  // the calls rather than by hiding them.
  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  /**
   * Remove the link, then RE-READ the session rather than assuming what changed.
   * The DELETE's body shape is not in the contract, so the truth about this
   * account after the call is whatever `/api/session` now says.
   */
  async function removeLink() {
    setStep("sending");
    const result = await sendJson<unknown>("DELETE", "/api/link/github");
    if (!result.ok) {
      setFailure({ error: result.error, fix: result.fix, detail: result.detail });
      setStep("idle");
      return;
    }
    setStep("done");
    await load();
  }

  if (failure && !session) {
    return <ErrorState error={failure.error} fix={failure.fix} detail={failure.detail} />;
  }
  if (!session) return <Skeleton label="Loading this account" rows={4} />;

  if (!session.signed_in) {
    return (
      <EmptyState
        headline="Nobody is signed in"
        action="Sign in with GitHub to see this account and the tenant it resolves to."
      >
        <Link href="/signin" className="btn" style={{ display: "inline-block" }}>
          Go to sign in
        </Link>
      </EmptyState>
    );
  }

  const linked = session.github_linked;

  return (
    <div style={{ display: "grid", gap: "var(--gap-6)", maxWidth: "var(--measure)" }}>
      <section className="card" style={{ display: "flex", gap: "var(--gap-4)", flexWrap: "wrap" }}>
        {session.image ? (
          // `alt=""` on purpose: the login is beside it, so a description would be
          // read out twice. `unoptimized` because the avatar host is not in
          // `next.config.mjs`'s image config, which is not this lane's file.
          <Image
            src={session.image}
            alt=""
            width={48}
            height={48}
            unoptimized
            style={{ borderRadius: "50%", border: "1px solid var(--border-strong)" }}
          />
        ) : null}
        <div style={{ minWidth: "12rem" }}>
          <p className="title" style={{ marginBottom: "var(--gap-1)" }}>
            {session.login ?? "Unknown login"}
          </p>
          <p style={{ margin: 0, color: "var(--text-muted)", fontSize: "var(--step-small)" }}>
            {session.name ?? "This account set no display name."}
          </p>
        </div>
      </section>

      <section className="card">
        <p className="eyebrow">Tenant</p>
        <p className="ident" style={{ margin: "0 0 var(--gap-2)" }}>
          {session.tenant_id ?? "none resolved"}
        </p>
        <p style={{ margin: 0, color: "var(--text-muted)", fontSize: "var(--step-small)" }}>
          Resolved by the server for this sign-in, and not editable here. No
          request this app sends carries a tenant, so a caller cannot name one.
        </p>
      </section>

      <section className="card">
        <p className="eyebrow">GitHub link</p>
        <Mark mark={linked ? LINK.linked : LINK.absent} explain />

        {failure ? (
          <div style={{ marginTop: "var(--gap-4)" }}>
            <ErrorState error={failure.error} fix={failure.fix} detail={failure.detail} />
          </div>
        ) : null}

        {step === "done" && !linked ? (
          <p
            role="status"
            style={{ margin: "var(--gap-4) 0 0", color: "var(--shipped)", fontSize: "var(--step-small)" }}
          >
            Link removed. Sign in again to re-link this account.
          </p>
        ) : null}

        {/* `idle` and `done` only. NOT `step !== "confirm"`, which would put this
            button back on screen while the DELETE is in flight -- a second click
            would send a second request against a link that may already be gone. */}
        {linked && (step === "idle" || step === "done") ? (
          <p style={{ margin: "var(--gap-4) 0 0" }}>
            <button type="button" className="btn btn-reject" onClick={() => setStep("confirm")}>
              Remove link
            </button>
          </p>
        ) : null}

        {linked && step === "confirm" ? (
          <div
            style={{
              marginTop: "var(--gap-4)",
              padding: "var(--gap-4)",
              border: "1px solid var(--refused)",
              borderLeftWidth: "3px",
              borderRadius: "4px",
              background: "var(--surface-sunken)",
            }}
          >
            <p style={{ margin: "0 0 var(--gap-3)", fontSize: "var(--step-body)" }}>
              Removing the link revokes the GitHub grant and drops the linked
              account. No further run can open a pull request or post a comment
              on any repository, including the ones in scope now.
            </p>
            <p style={{ margin: "0 0 var(--gap-4)", color: "var(--text-muted)", fontSize: "var(--step-small)" }}>
              Signing in again re-links this account. Runs already finished keep
              their record.
            </p>
            <div style={{ display: "flex", gap: "var(--gap-3)", flexWrap: "wrap" }}>
              <button
                type="button"
                className="btn btn-reject"
                onClick={() => void removeLink()}
              >
                Remove link
              </button>
              <button type="button" className="btn" onClick={() => setStep("idle")}>
                Keep the link
              </button>
            </div>
          </div>
        ) : null}

        {step === "sending" ? (
          <p role="status" style={{ margin: "var(--gap-4) 0 0", color: "var(--text-muted)", fontSize: "var(--step-small)" }}>
            Removing the link.
          </p>
        ) : null}
      </section>
    </div>
  );
}
