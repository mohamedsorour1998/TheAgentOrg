/**
 * THE DASHBOARD'S CHROME — header, wordmark, nav, sign out.
 *
 * This used to live in the ROOT layout, which meant `/signin` wore it too: a
 * visitor who was not signed in got Runs · Repositories · Costs · Account across
 * the top of the page asking them to sign in. Four links to screens that would
 * immediately redirect them back. Reported as wanting "a real login page".
 *
 * Scoping it to this group is the whole fix. `app/(auth)/` renders its own
 * chrome — deliberately almost none — and the two never mix.
 *
 * **A ROUTE GROUP CHANGES NO URL.** `(routes)` and `(auth)` are both invisible in
 * the path, so `/runs` and `/signin` are exactly where they were. That is what
 * makes this safe to do without touching the proxy matcher, every `<Link>`, or
 * the Cognito callback list — a rename of the segment would have broken all three.
 */

import { Shell } from "@/components/Shell";

export default function DashboardLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <Shell>{children}</Shell>;
}
