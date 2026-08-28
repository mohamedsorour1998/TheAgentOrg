/**
 * THE APP SHELL. Header, navigation, main, footer.
 *
 * A CLIENT COMPONENT, for exactly one reason: `usePathname` marks the current
 * link. That is worth the JavaScript because a person who cannot tell which
 * screen they are on navigates by trial, and `aria-current="page"` is also the
 * only way a screen reader answers "where am I?".
 *
 * Everything else here is markup. The shell holds no data, so it never needs to
 * know whether a fetch failed.
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * The six screens, in the order a person meets them.
 *
 * ONE DECLARATION -- `components/__tests__/nav.test.ts` asserts every route
 * directory under `app/(routes)/` appears here and vice versa, in both
 * directions. Lane I's `endpoints.ts` makes the same argument for its table: a
 * nav entry with no page is a promise, and a page with no nav entry is
 * unreachable except by typing the URL.
 */
export const NAV = [
  { href: "/runs", label: "Runs" },
  { href: "/repositories", label: "Repositories" },
  { href: "/costs", label: "Costs" },
  { href: "/account", label: "Account" },
] as const;

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="shell">
      {/* First focusable element on every page. */}
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <header className="shell-head">
        <div
          className="shell-head-inner"
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: "var(--gap-8)",
            flexWrap: "wrap",
          }}
        >
          <Link href="/runs" className="wordmark">
            The Agent Org<span>.</span>
          </Link>
          <nav className="nav" aria-label="Main">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                // Prefix match, so /runs/<id> still marks Runs as current. The
                // `/` guard stops /runs matching a hypothetical /runsomething.
                aria-current={
                  pathname === item.href || pathname.startsWith(`${item.href}/`)
                    ? "page"
                    : undefined
                }
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>

      <main className="shell-main" id="main">
        {children}
      </main>

      <footer className="shell-foot">
        <div className="shell-foot-inner">
          A security verdict here is computed by five lines of Python with no
          model in it. Findings report the index of an added line, not a position
          in the file.
        </div>
      </footer>
    </div>
  );
}
