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

import { NAV } from "@/components/nav";

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

          {/* SIGN OUT — the route existed and NOTHING LINKED TO IT.
              `app/api/auth/logout/route.ts` is 60 lines that clear this app's
              cookie with the exact attributes the callback set (a cookie deleted
              with a different `path` survives) and then redirect to Cognito's own
              `/logout` so its session ends too. All of it correct, all of it
              reachable only by typing the URL -- this repository's second named
              pattern, a correct answer nobody asks for, in a navigation bar.

              A PLAIN <a>, NOT <Link>. This is a route handler that redirects to
              another ORIGIN (the Cognito domain). `<Link>` attempts a client-side
              navigation, which cannot follow a cross-origin redirect, so sign-out
              would appear to do nothing while the session continued.

              HIDDEN ON /signin, because a sign-out control on the page you land on
              AFTER signing out invites a click that ends nothing and reads as a
              broken button. `pathname` is already loaded for `aria-current`. */}
          {pathname !== "/signin" ? (
            <a
              href="/api/auth/logout"
              className="nav"
              style={{ marginLeft: "auto", fontSize: "var(--step-small)" }}
            >
              Sign out
            </a>
          ) : null}
        </div>
      </header>

      <main className="shell-main" id="main">
        {children}
      </main>

    </div>
  );
}
