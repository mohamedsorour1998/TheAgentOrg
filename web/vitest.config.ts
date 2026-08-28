/**
 * Vitest configuration.
 *
 * `node` environment, not `jsdom`: everything Lane I owns runs on the server, and
 * a DOM would be a lie about where this code executes. Lane J will need `jsdom`
 * for component tests and should add a second project rather than change this one
 * -- a shared environment means a server module accidentally reaching for `window`
 * passes here and fails in production.
 */

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    // Only Lane I's own tests. Lane J's live under its own paths and it may add
    // them here; this pattern is deliberately explicit rather than a bare `**`,
    // so `node_modules` and `.next` cannot be walked.
    include: ["lib/**/*.test.ts", "app/api/**/*.test.ts"],
  },
  resolve: {
    alias: {
      // Mirrors tsconfig's `@/*`. Two declarations of one path mapping, which is
      // unavoidable -- vitest does not read tsconfig paths -- so a test asserts
      // they agree rather than trusting them to.
      "@": new URL(".", import.meta.url).pathname,
    },
  },
});
