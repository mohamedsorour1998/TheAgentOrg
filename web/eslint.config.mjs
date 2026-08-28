/**
 * ESLint flat config. eslint 9 requires this file; `.eslintrc` is not read.
 *
 * `eslint-config-next` v16 EXPORTS FLAT CONFIG DIRECTLY, so it is spread rather
 * than run through `FlatCompat`. Measured: the compat wrapper fails on this version
 * with a `JSON.stringify` error out of `config-validator.js` — it is validating a
 * flat array against the legacy `.eslintrc` schema. Reading its `exports` map is
 * what settled it; the arrays are already flat-config objects.
 *
 * Nothing is disabled here. Turning a rule off is the same shape as
 * `typescript.ignoreBuildErrors` — a gate reporting green for a thing it did not
 * check — so if a rule fires, fix the code.
 */

import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescriptConfig from "eslint-config-next/typescript";

// Named rather than exported anonymously: `import/no-anonymous-default-export` is
// on in this rule set, and silencing a rule to satisfy a config file would be the
// first exception in a config whose whole comment says there are none.
const config = [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts"],
  },
  ...coreWebVitals,
  ...typescriptConfig,
];

export default config;