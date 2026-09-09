/**
 * WHAT `verifySession` REFUSES. Every test here is a token an attacker would send.
 *
 * The one acceptance is at the bottom, and it is deliberately outnumbered: this
 * function's value is not that it produces a session, it is that it produces
 * `null` for everything else. `jose` does the cryptography and is not under test;
 * what is under test is that this file asks it for the right things and then
 * refuses everything it does not ask about.
 *
 * NO TEST REACHES THE NETWORK. `COGNITO_TEST_JWKS` injects a key set, and the
 * guard that makes that safe — the `NODE_ENV === "test"` gate — has its own file,
 * `cognito-anchor.test.ts`, because proving it needs module re-imports that would
 * make every test here order-dependent.
 */

import { CompactSign, SignJWT, exportJWK, generateKeyPair } from "jose";
import { beforeAll, describe, expect, it } from "vitest";

import { REVIEWER_ROLE } from "@/lib/authorize";
import { verifySession } from "@/lib/cognito";

const NOW = 1_788_400_000_000;
const ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TESTPOOL";
const CLIENT_ID = "test-client-id";
const SUBJECT = "7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d";

let sign: (
  claims: Record<string, unknown>,
  options?: { alg?: string; expSec?: number },
) => Promise<string>;
/** The private half, for tests that must sign RAW BYTES rather than go through
 *  `SignJWT`, which validates claims before signing and so cannot express the
 *  hazard in the non-finite-expiry test. */
let signingKey: CryptoKey;

beforeAll(async () => {
  const { privateKey, publicKey } = await generateKeyPair("RS256", {
    extractable: true,
  });
  signingKey = privateKey;
  const jwk = {
    ...(await exportJWK(publicKey)),
    kid: "test-kid",
    alg: "RS256",
    use: "sig",
  };
  process.env.COGNITO_ISSUER = ISSUER;
  process.env.COGNITO_CLIENT_ID = CLIENT_ID;
  process.env.COGNITO_TEST_JWKS = JSON.stringify({ keys: [jwk] });
  sign = async (claims, options = {}) =>
    new SignJWT({ token_use: "id", aud: CLIENT_ID, iss: ISSUER, ...claims })
      .setProtectedHeader({ alg: options.alg ?? "RS256", kid: "test-kid" })
      .setIssuedAt(Math.floor(NOW / 1000) - 10)
      .setExpirationTime(Math.floor(NOW / 1000) + (options.expSec ?? 3600))
      .sign(privateKey);
});

/** A well-formed reviewer's claims, for tests that vary one at a time.
 *
 *  `Record<string, unknown>` and not an inferred literal type, so a test can
 *  `delete` a claim — the freshly-signed-up case below needs exactly that, and an
 *  inferred type makes every key required (`TS2790`). */
const reviewer = (extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  sub: SUBJECT,
  "cognito:username": "a-real-person",
  "custom:role": REVIEWER_ROLE,
  "custom:tenant": "tenant-zero",
  ...extra,
});

/** The claims `sign()` adds on top. A test that builds a token BY HAND must add
 *  them itself, and forgetting to is how a refusal test passes for the wrong
 *  reason: `jose` checks the issuer long before it looks at `exp`, so a
 *  hand-built body with no `iss` is refused by the issuer check and proves
 *  nothing about the claim under test. Measured — the first version of the
 *  non-finite-expiry test below omitted these three and the `Number.isFinite`
 *  mutation came back INERT at `14 passed`. */
const ENVELOPE = { token_use: "id", aud: CLIENT_ID, iss: ISSUER };

describe("verifySession — refusals", () => {
  it("returns null for a missing cookie", async () => {
    expect(await verifySession(undefined, NOW)).toBeNull();
  });

  it("returns null for a token that is not a JWT at all", async () => {
    // Not a forged JWT — a literal sentence. The reference project measured this
    // exact string as a COMPLETE authentication bypass for every page read,
    // because the pages checked only that a cookie existed.
    expect(await verifySession("totally.forged.token", NOW)).toBeNull();
  });

  it("returns null for a token signed by the wrong key", async () => {
    const { privateKey } = await generateKeyPair("RS256", { extractable: true });
    const forged = await new SignJWT(reviewer())
      .setProtectedHeader({ alg: "RS256", kid: "test-kid" })
      .setExpirationTime(Math.floor(NOW / 1000) + 3600)
      .sign(privateKey);
    expect(await verifySession(forged, NOW)).toBeNull();
  });

  it("returns null for an expired token", async () => {
    expect(await verifySession(await sign(reviewer(), { expSec: -60 }), NOW)).toBeNull();
  });

  it("returns null for the wrong issuer", async () => {
    const token = await sign(reviewer({ iss: "https://evil.example" }));
    expect(await verifySession(token, NOW)).toBeNull();
  });

  it("returns null for the wrong audience", async () => {
    // A SECOND APP CLIENT IN THE SAME POOL signs with the SAME keys, so the
    // signature is genuinely valid and only `aud` separates them. This is why
    // `/api/auth/callback` verifies the token the exchange just handed it.
    const token = await sign(reviewer({ aud: "another-client" }));
    expect(await verifySession(token, NOW)).toBeNull();
  });

  it("returns null for an access token used as an ID token", async () => {
    // Cognito's access token carries no `custom:` attributes and no `aud` at all,
    // so accepting one authenticates a session with no authorisation basis.
    const token = await sign(reviewer({ token_use: "access" }));
    expect(await verifySession(token, NOW)).toBeNull();
  });

  it("returns null when there is no username to attribute a decision to", async () => {
    // `login` becomes `HumanDecision.by`. A blank attribution is
    // `approve_server`'s `by="ui-reviewer"` wearing a different value.
    for (const username of [undefined, "", "   ", 42, null, {}]) {
      const claims = reviewer();
      if (username === undefined) delete claims["cognito:username"];
      else claims["cognito:username"] = username;
      const token = await sign(claims);
      expect(await verifySession(token, NOW), String(username)).toBeNull();
    }
  });

  it("returns null for an unsigned (alg: none) token", async () => {
    // The classic JWT bypass, and the one that turns a verifier into a decoder.
    // Asserted rather than assumed.
    const header = Buffer.from(
      JSON.stringify({ alg: "none", kid: "test-kid" }),
    ).toString("base64url");
    const body = Buffer.from(
      JSON.stringify({
        ...ENVELOPE,
        ...reviewer(),
        exp: Math.floor(NOW / 1000) + 3600,
      }),
    ).toString("base64url");
    expect(await verifySession(`${header}.${body}.`, NOW)).toBeNull();
  });

  it("returns null for a token whose exp is not a finite number", async () => {
    // THE HAZARD `Number.isFinite` EXISTS FOR, and all three halves are asserted
    // here rather than argued: `1e400` parses to `Infinity`, `typeof` it is
    // `"number"` so a `typeof` guard admits it, and the resulting `expiresAt` is
    // one no `<=` comparison can ever call expired — a permanent session from a
    // token that says it never expires.
    //
    // Hand-built because `SignJWT` refuses it at signing time ('"exp" claim must
    // be a finite number'), which is exactly the point: the hazard is a token an
    // attacker crafts, not one `jose`'s builder would emit.
    //
    // `ENVELOPE` is spread in, and leaving it out is how the first version of
    // this test passed while proving nothing — see its definition above.
    const body = JSON.stringify({ ...ENVELOPE, ...reviewer() }).replace(
      /}$/,
      ',"exp":1e400}',
    );
    expect(JSON.parse(body).exp).toBe(Infinity);
    expect(typeof JSON.parse(body).exp).toBe("number");
    const forged = await new CompactSign(new TextEncoder().encode(body))
      .setProtectedHeader({ alg: "RS256", kid: "test-kid" })
      .sign(signingKey);
    expect(await verifySession(forged, NOW)).toBeNull();
  });
});

describe("verifySession — what it accepts, and what it carries", () => {
  it("accepts a correctly signed reviewer ID token", async () => {
    const session = await verifySession(await sign(reviewer()), NOW);
    expect(session).not.toBeNull();
    expect(session!.sub).toBe(SUBJECT);
    expect(session!.login).toBe("a-real-person");
    expect(session!.role).toBe(REVIEWER_ROLE);
    expect(session!.tenantId).toBe("tenant-zero");
    expect(session!.expiresAt).toBeGreaterThan(NOW);
  });

  it("ACCEPTS a freshly signed-up account with no role and no tenant", async () => {
    // THE ONE PLACE THIS LANE DIVERGES FROM THE REFERENCE, AND THE TEST THAT
    // PINS IT. Requirement 9 asks for self-service sign-up, so an account exists
    // before an administrator assigns anything. Refusing here would make that
    // account read as NOT SIGNED IN — `/api/session` would answer
    // `signed_in: false`, the screen would offer a sign-in button, and the button
    // would loop them back to the same answer with nothing saying why.
    //
    // Nothing is weakened, and `authorize.test.ts` is where that is proved: the
    // identity below is refused `no-role` by `authorizeSession`, so it
    // authenticates and authorises nothing.
    const claims = reviewer();
    delete claims["custom:role"];
    delete claims["custom:tenant"];
    const session = await verifySession(await sign(claims), NOW);
    expect(session).not.toBeNull();
    expect(session!.role).toBe("");
    expect(session!.tenantId).toBe("");
  });

  it("carries no email or name into the identity", async () => {
    // Inbound JWT claims are logged by AWS outside every redaction this
    // application has, and `sub` is opaque where an address is not.
    const token = await sign(
      reviewer({ email: "someone@example.com", name: "A Person" }),
    );
    const session = await verifySession(token, NOW);
    expect(JSON.stringify(session)).not.toContain("@example.com");
    expect(JSON.stringify(session)).not.toContain("A Person");
    expect(Object.keys(session!).sort()).toEqual([
      "expiresAt",
      "login",
      "role",
      "sub",
      "tenantId",
    ]);
  });

  it("never coerces a non-string claim into a role or a tenant", async () => {
    // `String({})` is `"[object Object]"`, which is a tenant id that would reach
    // `engine.acting_as`. Coercion invents a scope nobody set.
    const token = await sign(
      reviewer({ "custom:role": { evil: true }, "custom:tenant": 7 }),
    );
    const session = await verifySession(token, NOW);
    expect(session!.role).toBe("");
    expect(session!.tenantId).toBe("");
  });
});
