/**
 * Next.js configuration. Deliberately near-empty; every line is a decision.
 *
 * `web/` sits inside a Python repository whose root holds `agentorg/`, `tests/`
 * and `runs/` -- and `runs/` is ~10k gitignored files CLAUDE.md says never to
 * list. Next traces the filesystem from its own directory, so keeping the app
 * rooted at `web/` rather than the repository root is what keeps it out.
 */

/** @type {import("next").NextConfig} */
const nextConfig = {
  // POWERED-BY OFF. `X-Powered-By: Next.js` names the framework and its presence
  // to anything that scans. Free to remove and there is no reason to advertise.
  poweredByHeader: false,

  // TYPE AND LINT ERRORS FAIL THE BUILD, which is the default and is restated
  // here because the tempting escape hatches (`typescript.ignoreBuildErrors`,
  // `eslint.ignoreDuringBuilds`) are exactly the shape this repository refuses:
  // a gate that reports green for a thing it did not check. Do not add them.
  typescript: { ignoreBuildErrors: false },
  eslint: { ignoreDuringBuilds: false },
};

export default nextConfig;
