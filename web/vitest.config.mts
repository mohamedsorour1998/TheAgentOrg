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
