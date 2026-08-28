/**
 * Vitest configuration. Lane J adds this: the contract commit declared a `test`
 * script but no config, so `@/` did not resolve and every test importing the
 * contract would fail on the path rather than on its subject.
 *
 * `environment: "node"` for now -- the tests here are over the vocabulary tables
 * and over source text, neither of which needs a DOM. A component test that
 * renders would need jsdom plus a testing library, and adding two dependencies
 * to assert a heading exists is a poor trade against tests that pin the
 * distinctions which can actually be wrong.
 */

import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  // NO `esbuild: { jsx: ... }` OVERRIDE, and that is a measured result rather than
  // an omission: setting `jsx: "automatic"` here did NOT rescue a test importing a
  // `.tsx` module. The real cause was `tsconfig.json` shipping `jsx: "preserve"`,
  // which `next build` reports as a MANDATORY change to `react-jsx` (Next 16 uses
  // the React automatic runtime). With the corrected value a probe test importing
  // `Shell.tsx` passes, so no override is needed here at all.
  //
  // See `components/nav.ts` for why the failure is worth remembering: it presented
  // as `Test Files 1 failed | 1 passed` with `Tests 15 passed (15)` — every test
  // green, one whole file never executed. Read the FILE count.
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["**/__tests__/**/*.test.ts"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
