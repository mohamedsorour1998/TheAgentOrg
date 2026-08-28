/**
 * ESLint configuration. Lane J adds this file because `package.json` declares a
 * `lint` script and there was no config for it to read -- `npm run lint` exited
 * with "ESLint couldn't find an eslint.config.(js|mjs|cjs) file", so the gate
 * `next.config.mjs` refuses to disable was one that could not run at all.
 *
 * That is the distinction this repository cares about most: a check that cannot
 * run is not a check that passes, and the two look identical from a terminal
 * nobody reads closely.
 *
 * NO `FlatCompat`, AND THAT WAS MEASURED RATHER THAN ASSUMED. The documented
 * recipe for this config wraps the Next preset in `@eslint/eslintrc`'s
 * `FlatCompat`, which needs three extra dev dependencies and failed here inside
 * the compat layer's own schema validator. `eslint-config-next@16.3.3` already
 * exports flat config -- verified, not guessed:
 *
 *     node -e "import('eslint-config-next/core-web-vitals')
 *              .then(m => console.log(Array.isArray(m.default)))"
 *     -> true      (4 entries)
 *
 * So the preset spreads directly and this app adds ZERO dependencies to lint.
 * Check the export shape before reaching for a compat shim.
 *
 * NEXT'S OWN RULES, NOT A HAND-PICKED SET. The preset carries the rules that
 * catch real Next.js defects -- a client component importing a server-only
 * module, a hook called conditionally. Cherry-picking here would restate a
 * policy the framework ships, and the two copies would drift. Mirroring the
 * Python side: no `[tool.ruff]` section there, no rule selection here.
 */

import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

/** The flat config, named rather than exported anonymously -- the preset's own
 *  `import/no-anonymous-default-export` rule flags a bare array literal here,
 *  and a config that warns on itself is a poor advertisement for the gate. */
const config = [
  {
    // `.next/` is generated per build and fails every rule; linting it reports
    // problems nobody can fix. `next-env.d.ts` is likewise generated.
    ignores: ["node_modules/**", ".next/**", "out/**", "next-env.d.ts"],
  },
  ...coreWebVitals,
  ...typescript,
];

export default config;
