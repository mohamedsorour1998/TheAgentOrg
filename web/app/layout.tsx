/**
 * The root layout. MINIMAL ON PURPOSE -- Lane J owns the visual design.
 *
 * This file is shared scaffolding: Lane I creates it because Lane I goes first and
 * Next.js refuses to build without it. Everything here is structural (the html
 * element, the language, the metadata) and nothing is aesthetic. Lane J should
 * extend it -- fonts, the shell, navigation -- rather than replace it, and should
 * not need to touch anything above the `<body>` children.
 *
 * `lang="en"` is not decoration: without it a screen reader guesses the language
 * and pronounces identifiers in the wrong one.
 */

import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "The Agent Org",
  description:
    "Five role agents walk a ticket through three human gates; a deterministic " +
    "security rule decides whether it ships.",
};

/**
 * **THE SHELL MOVED OUT OF HERE, AND THAT WAS A REPORTED BUG.** This layout used
 * to wrap every page in `<Shell>` — header, wordmark and the four-item nav — so
 * `/signin` rendered with Runs · Repositories · Costs · Account across the top and
 * read as another tab of a product the visitor had not entered yet. Reported as
 * wanting "a real login page" rather than being dropped into the app chrome.
 *
 * The reference deployment (`~/sorour/AgentsforHumansHackathon`) has the identical
 * structure and never hit this, for one reason: its `/login` is a `redirect()` to
 * the hosted UI, so the shell never renders. Ours is a page people actually look
 * at, so the shell has to be scoped rather than global.
 *
 * Two groups now own their own chrome:
 *
 *     app/(routes)/layout.tsx   the dashboard — Shell, nav, sign out
 *     app/(auth)/layout.tsx     sign in and register — no nav, nothing to leave to
 *
 * `colorScheme: "dark"` stays on the html element rather than in CSS because it
 * changes what the BROWSER draws, not what this app draws: scrollbars, the caret,
 * and the default styling of a form control before any rule applies. On a dark
 * surface without it a native `<select>` renders as a light rectangle, and the
 * browser's own scrollbar stays white down the side of the page.
 */
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" style={{ colorScheme: "dark" }}>
      <body>{children}</body>
    </html>
  );
}
