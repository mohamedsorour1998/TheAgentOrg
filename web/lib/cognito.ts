/**
 * TURNING A COOKIE INTO AN IDENTITY, OR INTO NOTHING.
 *
 * `verifySession` returns a `TokenIdentity` or `null`. There is no middle value —
 * a token that fails any check produces `null`, and every caller refuses on a
 * null session. That is deliberate: a partially-trusted session is a thing nobody
 * can reason about, and this is the surface that can open a security gate.
 *
 * Every check here is one an attacker would otherwise skip: the signature against
 * Cognito's published keys, the issuer, the audience, the expiry, that it is an
 * **ID** token and not an access token, and that the claims it carries are of the
 * shape the rest of the application reads. `jose` performs the cryptography; the
 * value this file adds is refusing everything else.
 *
 * WHAT IS AUTHENTICITY AND WHAT IS AUTHORISATION — READ `authorize.ts` FIRST.
 * =========================================================================
 * This file decides *is this token genuinely from our pool, about a real
 * account*. It does **not** decide whether that account may act: the role is
 * carried, not compared, and `authorizeSession` compares it. The reference
 * implementation folds the two together and is right to, because its pool refuses
 * self-service sign-up; requirement 9 asks for sign-up here, so a freshly
 * signed-up account must be able to be *signed in and not yet authorised*. The
 * long form of that argument is in `authorize.ts`'s header.
 *
 * WHAT DOES NOT REACH `TokenIdentity`: `email`, `name`, `phone_number`, and every
 * other claim the pool may carry. Inbound JWT claims are logged by AWS outside
 * every redaction this application has, and `HumanDecision.by` is written to an
 * append-only log with no deletion path. The four fields carried are the four
 * something reads.
 */

import { createRemoteJWKSet, jwtVerify, type JWTPayload } from "jose";

import type { TokenIdentity } from "./authorize";

/**
 * The cookie the ID token rides in.
 *
 * NOT `authjs.session-token`. That name belonged to a DATABASE session strategy
 * where the cookie was an opaque row key and the row was the session; this cookie
 * carries the token itself. Reusing the name would mean a browser that still held
 * an Auth.js cookie from a previous deployment presenting it here, where it would
 * fail verification and produce `null` — correct, but indistinguishable from
 * "your session expired" for somebody who never signed out. A new name makes the
 * old cookie inert instead of confusing.
 */
export const SESSION_COOKIE = "agentorg_session";

/**
 * How long the cookie lives. ONE HOUR, and it is now a security setting rather
 * than a convenience.
 *
 * The Auth.js configuration this replaces used thirty days, chosen against what
 * the session AUTHORISES. That reasoning still holds and now cuts harder, because
 * the TENANT rides on the token: `web/lib/session.ts` argued that carrying a
 * tenant on the session means "revoking somebody's membership would leave their
 * live session still scoped to the tenant they were removed from, with nothing
 * anywhere saying so". A verified claim has exactly that property, and the token's
 * lifetime IS the revocation latency. One hour is the bound this deployment
 * accepts; it must not be raised without re-reading `web/lib/tenant.ts`.
 */
export const SESSION_MAX_AGE_SECONDS = 60 * 60;

/** `custom:` attributes are set by Cognito's admin API, server-side, never by the client. */
const ROLE_CLAIM = "custom:role";
const TENANT_CLAIM = "custom:tenant";
/** The pool's own username claim. Present on every Cognito ID token. */
const LOGIN_CLAIM = "cognito:username";

function issuer(): string {
  const value = process.env.COGNITO_ISSUER;
  if (!value) throw new Error("COGNITO_ISSUER is not set.");
  return value;
}

function clientId(): string {
  const value = process.env.COGNITO_CLIENT_ID;
  if (!value) throw new Error("COGNITO_CLIENT_ID is not set.");
  return value;
}

function domain(): string {
  const value = process.env.COGNITO_DOMAIN;
  if (!value) throw new Error("COGNITO_DOMAIN is not set.");
  return value;
}

type KeyResolver = Parameters<typeof jwtVerify>[1];

/** True only for a finite number, and it tells TypeScript so.
 *
 *  `Number.isFinite` is declared `(number: unknown): boolean` — a plain boolean,
 *  not a type predicate — so `if (!Number.isFinite(payload.exp)) return null;`
 *  leaves `payload.exp` typed `number | undefined` and the multiplication below
 *  fails to compile with `TS18048`. Measured in the reference implementation,
 *  whose plan shipped exactly that and did not pass `tsc`.
 *
 *  Wrapping it rather than adding `typeof exp === "number"` alongside keeps
 *  `Number.isFinite` the ONLY runtime check, which is the point: `typeof
 *  Infinity` is `"number"`, so a `typeof` test alone accepts `exp: 1e400` — a
 *  token `jose` genuinely verifies, whose `expiresAt` no `<=` comparison can ever
 *  call expired. `Number.isFinite` refuses `undefined`, `null`, a numeric
 *  *string*, `NaN` and both infinities without coercing anything. */
function isFiniteNumber(value: unknown): value is number {
  return Number.isFinite(value);
}

/** A claim as a trimmed string, or `""`. Never `undefined`, never a coercion.
 *
 *  A non-string claim — a number, an object, an array — becomes `""` rather than
 *  `String(value)`. Coercion would invent a role or a tenant nobody set, and
 *  `"[object Object]"` is a tenant id that would reach `engine.acting_as`. */
function claim(payload: JWTPayload, name: string): string {
  const value = payload[name];
  return typeof value === "string" ? value.trim() : "";
}

let cachedKeys: KeyResolver | undefined;
function keys(): KeyResolver {
  // Tests inject a key set so no test reaches the network. The `NODE_ENV` guard
  // is the load-bearing part: without it, setting COGNITO_TEST_JWKS on the
  // deployed app replaces Cognito's real key set with an attacker-supplied one,
  // and every forged token then verifies WITH A VALID SIGNATURE — nothing
  // downstream would notice, because against that key set the session genuinely
  // is authentic. An environment variable that swaps out a trust anchor must
  // never be readable in production. Verified: vitest sets NODE_ENV="test";
  // `next build` and `next start` set "production".
  const injected =
    process.env.NODE_ENV === "test" ? process.env.COGNITO_TEST_JWKS : undefined;
  if (injected) {
    const parsed = JSON.parse(injected) as { keys: Record<string, unknown>[] };
    return (async (header: { kid?: string }) => {
      const { importJWK } = await import("jose");
      // SELECT BY `kid` AND REFUSE ON A MISS — no `?? keys[0]` fallback. A real
      // Cognito pool publishes TWO signing keys, one for ID tokens and one for
      // access tokens, so a resolver that fell back to the first key would verify
      // a token signed by any key in the set. This path is test-only, so the
      // failure is not a production bypass; it is worse in a subtler way — it
      // would make the suite unable to tell a correct verifier from one that
      // ignores `kid`, which is a check that cannot fail.
      const jwk = parsed.keys.find((k) => k.kid === header.kid);
      if (!jwk) throw new Error(`no key for kid ${String(header.kid)}`);
      return importJWK(jwk as never, "RS256");
    }) as unknown as KeyResolver;
  }
  cachedKeys ??= createRemoteJWKSet(
    new URL(`${issuer()}/.well-known/jwks.json`),
  ) as unknown as KeyResolver;
  return cachedKeys;
}

/**
 * A cookie value, or an identity, or nothing.
 *
 * `nowMs` is a parameter for the same reason it is one in `authorize.ts`: a check
 * that reads its own clock cannot be driven to its boundary by a test.
 */
export async function verifySession(
  idToken: string | undefined,
  nowMs: number = Date.now(),
): Promise<TokenIdentity | null> {
  if (!idToken) return null;

  let payload: JWTPayload;
  try {
    ({ payload } = await jwtVerify(idToken, keys(), {
      issuer: issuer(),
      audience: clientId(),
      // `jose` refuses `alg: "none"` and anything not listed here. A Cognito
      // pool's `id_token_signing_alg_values_supported` is `["RS256"]` and nothing
      // else, so this allowlist restricts nothing legitimate — and without it the
      // HS256 confusion attack (signing with the public key's modulus as an HMAC
      // secret) is on the table.
      algorithms: ["RS256"],
      currentDate: new Date(nowMs),
    }));
  } catch {
    // Any cryptographic or claim failure is the same answer: no session. The
    // distinctions matter to a developer and not to a caller, and answering them
    // separately would tell an attacker which half of a forgery to fix.
    return null;
  }

  // An ACCESS token carries no `custom:` attributes and, on Cognito, no `aud` at
  // all — so accepting one would authenticate a session with no authorisation
  // basis behind it and no audience binding.
  if (payload.token_use !== "id") return null;

  const sub = payload.sub;
  if (typeof sub !== "string" || sub.trim() === "") return null;

  // `login` becomes `HumanDecision.by`. A blank one is refused rather than
  // defaulted: `approve_server`'s `by="ui-reviewer"` is the constant this whole
  // surface exists to replace, and a blank attribution is the same defect wearing
  // a different value.
  const login = claim(payload, LOGIN_CLAIM);
  if (login === "") return null;

  if (!isFiniteNumber(payload.exp)) return null;

  // ROLE AND TENANT ARE CARRIED, NOT COMPARED. A freshly signed-up account has
  // neither, and `""` is the honest answer for "no administrator has assigned one
  // yet". `authorizeSession` refuses on the role and `authz.decide` refuses on
  // the tenant, each with a code that says which.
  return {
    sub,
    login,
    role: claim(payload, ROLE_CLAIM),
    tenantId: claim(payload, TENANT_CLAIM),
    expiresAt: payload.exp * 1000,
  };
}

/** The cookie carrying the OAuth `state` between `/api/auth/signin` and the callback. */
export const OAUTH_STATE_COOKIE = "agentorg_oauth_state";

/**
 * Where to send a signed-out visitor. `redirect_uri` must be a registered callback.
 *
 * `/login` is the CLASSIC hosted UI path, which a pool at `ManagedLoginVersion: 1`
 * serves. Managed login (version 2) uses a different URL shape and needs a
 * branding style, so a pool provisioned at version 2 needs this builder rewritten
 * and the round trip re-tested — it is not a cosmetic setting.
 *
 * `state` IS CARRIED, AND THE REFERENCE IMPLEMENTATION DOES NOT CARRY IT. That is
 * the one deliberate addition this lane makes to the reference's flow, and the
 * reason is what this surface can do: `POST /api/approvals` opens a security gate,
 * so login-CSRF is not an abstract concern. Without `state` an attacker can hand a
 * victim a callback URL carrying the ATTACKER's authorization code; the victim's
 * browser completes the exchange and is silently signed in as the attacker, and
 * every gate they then approve is recorded against the attacker's identity. The
 * callback compares this value against a cookie the same browser was given and
 * refuses on any mismatch.
 *
 * PKCE IS **NOT** IMPLEMENTED, and that is a stated gap rather than an oversight.
 * It defends a different attack — an authorization code intercepted in transit or
 * through a redirect leak — and it interacts with how the app client is
 * provisioned. The reference implements neither; this lane adds the one that costs
 * a cookie and names the one that does not.
 *
 * NAMED ARGUMENTS, AND THAT IS A MEASURED CHOICE RATHER THAN A STYLE ONE. The
 * first draft of this function took `(redirectUri: string, state: string)` and
 * the first caller written against it passed them the other way round —
 * `hostedUiUrl(state, redirectUri)`. **`tsc` accepts that silently**, because
 * both are `string`, and the failure it produces is a redirect to a URL that is
 * not a registered callback: Cognito answers `redirect_mismatch`, which reads as
 * a misconfigured pool rather than as a swapped argument. Two same-typed
 * positional parameters on a security redirect is a defect the compiler cannot
 * see; an object makes the swap unspellable.
 */
export function hostedUiUrl(options: {
  redirectUri: string;
  state: string;
}): string {
  const params = new URLSearchParams({
    client_id: clientId(),
    response_type: "code",
    // `openid` alone. `email` and `profile` would put an address in a token this
    // application decodes and AWS logs, and nothing reads either.
    scope: "openid",
    redirect_uri: options.redirectUri,
    state: options.state,
  });
  return `${domain()}/login?${params.toString()}`;
}

/** Where to send somebody signing out. Clears COGNITO's session, not ours. */
export function logoutUrl(landing: string): string {
  const params = new URLSearchParams({
    client_id: clientId(),
    logout_uri: landing,
  });
  return `${domain()}/logout?${params.toString()}`;
}

/**
 * Exchange an authorization code for an ID token. SERVER-SIDE ONLY.
 *
 * `COGNITO_CLIENT_SECRET` IS OPTIONAL AND COMES FROM THE ENVIRONMENT, NEVER FROM
 * A COMMITTED FILE. An app client provisioned without a secret is the shape the
 * reference deployment ships and needs none; a client provisioned *with* one must
 * send it, and Cognito's token endpoint takes it as HTTP Basic rather than as a
 * body field. Reading it here rather than requiring it means one code path serves
 * both, and an absent value is the public-client case rather than a failure —
 * which is the only ambiguity worth stating: if the pool HAS a secret and this
 * variable is unset, the exchange answers `invalid_client` and this function
 * returns `null`, so the symptom is "sign-in did not complete" rather than
 * anything naming the cause. That is the one link in the chain a missing variable
 * makes silent, and it is why Lane Q's spec must carry the variable whenever the
 * client is provisioned with a secret.
 */
export async function exchangeCode(
  code: string,
  redirectUri: string,
): Promise<string | null> {
  const secret = process.env.COGNITO_CLIENT_SECRET ?? "";
  const headers: Record<string, string> = {
    "Content-Type": "application/x-www-form-urlencoded",
  };
  if (secret.trim() !== "") {
    const basic = Buffer.from(`${clientId()}:${secret}`).toString("base64");
    headers.Authorization = `Basic ${basic}`;
  }
  const response = await fetch(`${domain()}/oauth2/token`, {
    method: "POST",
    headers,
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: clientId(),
      code,
      redirect_uri: redirectUri,
    }),
  });
  if (!response.ok) return null;
  const body = (await response.json()) as { id_token?: string };
  return body.id_token ?? null;
}
