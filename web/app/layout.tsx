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

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
