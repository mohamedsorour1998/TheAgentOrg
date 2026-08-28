/**
 * THE FOUR SCREENS AND THEIR ORDER. A `.ts` file, not part of `Shell.tsx`.
 *
 * The nav test asserts every route on disk has an entry here and vice versa, so it
 * imports this constant. Keeping it out of `Shell.tsx` means that test needs no
 * React, no DOM and no JSX transform to check a fact about the filesystem — which
 * is the right shape for it regardless, and it also sidesteps a trap worth
 * recording:
 *
 * IMPORTING A `.tsx` MODULE FROM A TEST FAILED, AND THE REASON WAS A STALE
 * TSCONFIG RATHER THAN A NEXT REQUIREMENT. The contract's `tsconfig.json` shipped
 * `jsx: "preserve"`; vite could not parse the result and the whole suite FILE
 * failed to load. `next build` then reported `jsx was set to react-jsx` as a
 * MANDATORY change — Next 16 uses the React automatic runtime — and with that
 * value a probe test importing `Shell.tsx` passes. So `preserve` was simply wrong,
 * and an `esbuild: { jsx: "automatic" }` override in the vitest config did not
 * rescue it.
 *
 * The failure is worth naming for how it READ:
 *
 *     Test Files  1 failed | 1 passed (2)
 *     Tests      15 passed (15)
 *
 * Every test passing, with an entire file never executed. READ THE FILE COUNT — a
 * test count cannot tell you a suite ran.
 *
 * A nav table is data. Nothing here renders, so nothing here needs a component.
 */

/** The four screens in the nav, in the order a person meets them. */
export const NAV = [
  { href: "/runs", label: "Runs" },
  { href: "/repositories", label: "Repositories" },
  { href: "/costs", label: "Costs" },
  { href: "/account", label: "Account" },
] as const;
