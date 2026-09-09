/**
 * THE TRUST ANCHOR. One property, and it is the most dangerous line in the lane.
 *
 * `COGNITO_TEST_JWKS` REPLACES THE KEY SET EVERY SESSION IS VERIFIED AGAINST. If
 * production code read it, setting that one variable on the deployed app would
 * make every forged token verify **with a valid signature** — and nothing
 * downstream would notice, because against that key set the session genuinely is
 * authentic. Every refusal in `cognito.test.ts` would still pass. So the guard is
 * `process.env.NODE_ENV === "test"`, and proving it means proving the **same
 * token** stops verifying when only `NODE_ENV` changes.
 *
 * ITS OWN FILE, because the proof needs `vi.resetModules()` and a re-import:
 * `cachedKeys` is module-level, so a resolver cached under one environment would
 * answer for the other. Doing that inside `cognito.test.ts` would make every test
 * there order-dependent.
 *
 * NO TEST HERE REACHES THE NETWORK, and that is arranged rather than hoped for.
 * The production arm's whole point is that it falls through to
 * `createRemoteJWKSet`, which fetches. `COGNITO_ISSUER` is pointed at
 * `http://127.0.0.1:1` for the duration — a port nothing listens on, so the fetch
 * is refused locally with no DNS and no outbound packet. Pointing it at the real
 * `cognito-idp.us-east-1.amazonaws.com` would work too, and would make this suite
 * fail on a machine with no internet, which is exactly the property
 * `tests/conftest.py`'s six guards exist to protect on the Python side.
 */

import { SignJWT, exportJWK, generateKeyPair } from "jose";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { REVIEWER_ROLE } from "@/lib/authorize";
import { verifySession } from "@/lib/cognito";

const NOW = 1_788_400_000_000;
/** A port nothing listens on. See the header: this keeps the fetch local. */
const ISSUER = "http://127.0.0.1:1";
const CLIENT_ID = "test-client-id";

let token: string;

beforeAll(async () => {
  const { privateKey, publicKey } = await generateKeyPair("RS256", {
    extractable: true,
  });
  const jwk = {
    ...(await exportJWK(publicKey)),
    kid: "test-kid",
    alg: "RS256",
    use: "sig",
  };
  process.env.COGNITO_ISSUER = ISSUER;
  process.env.COGNITO_CLIENT_ID = CLIENT_ID;
  process.env.COGNITO_TEST_JWKS = JSON.stringify({ keys: [jwk] });
  token = await new SignJWT({
    token_use: "id",
    aud: CLIENT_ID,
    iss: ISSUER,
    sub: "7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d",
    "cognito:username": "a-real-person",
    "custom:role": REVIEWER_ROLE,
    "custom:tenant": "tenant-zero",
  })
    .setProtectedHeader({ alg: "RS256", kid: "test-kid" })
    .setIssuedAt(Math.floor(NOW / 1000) - 10)
    .setExpirationTime(Math.floor(NOW / 1000) + 3600)
    .sign(privateKey);
});

/** Set `NODE_ENV` in a way `process.env` actually accepts.
 *
 *  It is typed read-only on `ProcessEnv`, so a plain assignment does not compile
 *  — hence `defineProperty`. Node then requires the descriptor to be
 *  configurable **and** writable **and** enumerable; the reference measured that
 *  every subset throws `'process.env' only accepts a configurable, writable, and
 *  enumerable data descriptor`. Its own draft set `configurable` alone, which
 *  threw on the way in AND again in the `finally`, replacing the original error —
 *  and, worse, left `NODE_ENV` as `"test"`, so the assertion would have been
 *  checking the injected-keys path against itself. */
function setNodeEnv(value: string | undefined): void {
  Object.defineProperty(process.env, "NODE_ENV", {
    value,
    configurable: true,
    writable: true,
    enumerable: true,
  });
}

describe("the trust anchor", () => {
  it("ignores an injected key set outside a test environment", async () => {
    expect(await verifySession(token, NOW)).not.toBeNull();

    const original = process.env.NODE_ENV;
    try {
      setNodeEnv("production");
      vi.resetModules();
      const { verifySession: production } = await import("@/lib/cognito");
      // PROVE THE ENVIRONMENT ACTUALLY CHANGED BEFORE TRUSTING THE REFUSAL. A
      // silently-refused write would make the assertion below check the
      // injected-keys path against itself and pass for the wrong reason.
      expect(process.env.NODE_ENV).toBe("production");
      expect(await production(token, NOW)).toBeNull();
    } finally {
      setNodeEnv(original);
      vi.resetModules();
    }

    // AND THE GUARD IS NOT ONE-WAY. The same token verifies again once the
    // environment is back. A test that left the module permanently refusing
    // would look identical to one that proved the guard.
    expect(await verifySession(token, NOW)).not.toBeNull();
  });
});
