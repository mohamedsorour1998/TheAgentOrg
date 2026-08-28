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

import { Shell } from "@/components/Shell";

import "./globals.css";

export const metadata: Metadata = {
  title: "The Agent Org",
  description:
    "Five role agents walk a ticket through three human gates; a deterministic " +
    "security rule decides whether it ships.",
};

/**
 * LANE J EXTENDS BELOW. Nothing structural above is changed -- the `lang`
 * attribute, the metadata and the stylesheet import are Lane I's and stay as
 * they are. The only addition is the shell around `children`, which is the
 * header, navigation and footer every screen shares.
 *
 * `colorScheme: "dark"` is on the html element rather than in CSS because it
 * changes what the BROWSER draws, not what this app draws: scrollbars, the
 * caret, and the default styling of a form control before any rule applies. On
 * a dark surface without it a native `<select>` renders as a light rectangle,
 * and the browser's own scrollbar stays white down the side of the page.
 */
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" style={{ colorScheme: "dark" }}>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
