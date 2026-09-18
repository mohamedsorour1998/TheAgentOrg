/**
 * SIGN IN AND REGISTER — a real front door, with none of the app's chrome.
 *
 * ## What this layout deliberately does NOT render
 *
 * No nav. A person who is not signed in cannot use Runs, Repositories, Costs or
 * Account, and every one of those links would bounce them straight back here —
 * four controls whose only possible outcome is a redirect. No sign-out either:
 * there is nothing to sign out of. The reference deployment never had to decide
 * this because its `/login` is a `redirect()` and its shell never renders.
 *
 * ## Already signed in? Go to the platform
 *
 * Reported directly: *"when i enter i see the platform"*. Without this, signing in
 * and then navigating back to `/signin` shows a sign-in form to somebody who is
 * signed in — which reads as the session having been lost, and invites a second
 * sign-in that was never needed.
 *
 * **THE REDIRECT IS HERE RATHER THAN IN EACH PAGE** so a third auth screen cannot
 * be added without it. `lib/guard.ts` makes the same argument from the other side:
 * both directions of this gate belong in one place each, or they drift.
 *
 * `redirect()` throws a control-flow error Next catches, so it must not sit inside
 * a `try`.
 *
 * ## The look is the reference deployment's discipline, applied to our subject
 *
 * Its stylesheet states the rule this page follows: *"muted and administrative on
 * purpose: this is a work queue a caseworker looks at all day, not a marketing
 * page. The one saturated colour is reserved for 'a human must decide'."*
 *
 * So: one saturated colour, spent once, on the single action. `--accent` appears
 * on the GitHub button and the wordmark's full stop and nowhere else on this page
 * — everything else is text, muted text, and hairlines. The dashboard sprays cyan
 * on every structural mark; a front door with one job should not.
 *
 * And its type rule, which this project already had tokens for and was not
 * applying consistently: **mono is for what the SYSTEM wrote** — the product's own
 * name, a stage, an id — **sans is for sentences a PERSON reads.**
 */

import { redirect } from "next/navigation";

import { currentIdentity } from "@/lib/session";

export default async function AuthLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  if ((await currentIdentity()) !== null) {
    redirect("/runs");
  }

  return (
    <main
      style={{
        minHeight: "100dvh",
        display: "grid",
        // `start` rather than `center`: a centred form jumps vertically between
        // the short sign-in page and the taller register form, which reads as the
        // page reloading when it is only a link.
        alignContent: "start",
        justifyItems: "center",
        gap: "var(--gap-8)",
        padding: "clamp(var(--gap-8), 12vh, 8rem) var(--gap-6) var(--gap-12)",
      }}
    >
      <div style={{ width: "100%", maxWidth: "26rem", display: "grid", gap: "var(--gap-8)" }}>
        {children}
      </div>
    </main>
  );
}
