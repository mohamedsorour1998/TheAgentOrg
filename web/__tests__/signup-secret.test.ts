/**
 * The sign-up secret's key names must agree across the two languages.
 *
 * `infra/cognito/provision._store_signup_secret` WRITES the payload in Python;
 * `web/lib/signup.ts` READS it in TypeScript. Neither can import the other, so the
 * key names are a second declaration — the same situation as the DynamoDB key
 * layout, and this test is the same answer.
 *
 * **IT EXISTS BECAUSE THE DRIFT ALREADY HAPPENED.** The writer stored `client_id`
 * and `client_secret`; the reader looked for `clientId` and `clientSecret`. Measured
 * against the deployed app on the first live sign-up:
 *
 *     http 400
 *     {"error":"sign-up is not configured",
 *      "detail":"theagentorg-shared-cognito-signup-client has no clientId/clientSecret"}
 *
 * The refusal was correct and specific, which is the only reason it took a minute
 * rather than an hour — but nothing before deployment could have caught it, because
 * each file was internally consistent. That is the whole shape of a second
 * declaration: two copies that keep agreeing until one moves.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const REPO_ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..", "..");
const PROVISION = readFileSync(
  path.join(REPO_ROOT, "infra", "cognito", "provision.py"),
  "utf8",
);
const SIGNUP = readFileSync(path.join(REPO_ROOT, "web", "lib", "signup.ts"), "utf8");

describe("the sign-up secret's payload", () => {
  it("reads both files at all", () => {
    // Anti-vacuity: every assertion below is a regex over these strings, and an
    // unreadable file would make each one match nothing and pass.
    expect(PROVISION).toContain("_store_signup_secret");
    expect(SIGNUP).toContain("WIRE_CLIENT_ID");
  });

  it("writes and reads the same key names", () => {
    const written = PROVISION.match(
      /payload = json\.dumps\(\{"([a-z_]+)": client_id, "([a-z_]+)": client_secret\}\)/,
    );
    expect(
      written,
      "provision.py no longer builds the payload in the shape this test knows; if " +
        "the writer changed, change the reader in web/lib/signup.ts with it",
    ).not.toBeNull();

    const readId = SIGNUP.match(/const WIRE_CLIENT_ID = "([a-z_]+)"/)?.[1];
    const readSecret = SIGNUP.match(/const WIRE_CLIENT_SECRET = "([a-z_]+)"/)?.[1];

    expect(readId).toBe(written?.[1]);
    expect(readSecret).toBe(written?.[2]);
  });

  it("names the same secret in both places", () => {
    const pySecret = PROVISION.includes("spec.SIGNUP_SECRET_NAME");
    expect(pySecret, "provision.py must take the secret name from the spec").toBe(true);
    // The TypeScript restates the literal because it cannot import the spec. If it
    // drifts, every sign-up fails with a message about Secrets Manager.
    expect(SIGNUP).toContain("theagentorg-shared-cognito-signup-client");
  });
});
