/**
 * THE SESSION A GITHUB SIGN-IN PRODUCES — a SECOND cookie, deliberately.
 *
 * ## Why not put this in the existing session
 *
 * `agentorg_session` holds a Cognito ID token, verified RS256 against Cognito's
 * published keys. A GitHub sign-in produces no such token: Cognito **cannot**
 * federate GitHub, because GitHub is OAuth2 and issues no `id_token` for Cognito to
 * consume.
 *
 * The tempting move is to teach `verifySession` a second token type. **That is the
 * JWT algorithm-confusion hole**, and it is worth naming rather than avoiding by
 * instinct: a verifier that accepts both RS256 and HS256 can be handed a token
 * signed with HMAC using the *public* RSA key as the secret — which the attacker
 * has, because it is published — and it verifies. So the two never meet. Separate
 * cookie, separate key, separate verifier, and `cognito.verifySession` is not
 * touched by this file at all.
 *
 * ## Encrypted (JWE), not merely signed (JWS)
 *
 * This session carries the GitHub **user access token**, because the repository
 * picker needs it to list installations. A signed JWT is base64, not ciphertext, so
 * anyone who obtained the cookie could read that token straight out of it and use it
 * against GitHub directly — outliving this session and reaching repositories this
 * application never touches. Encrypted, the cookie can be REPLAYED at us but the
 * credential inside cannot be extracted. `dir` + `A256GCM`, one key, from Secrets
 * Manager.
 *
 * That is a real reduction and not a complete defence, and the honest statement is
 * both halves: a stolen cookie is still a stolen session until it expires.
 *
 * ## One hour, and no revocation — stated rather than implied
 *
 * `MAX_AGE_SECONDS` matches `cognito.SESSION_MAX_AGE_SECONDS`, so both kinds of
 * session age out alike. A self-contained token cannot be revoked before it expires;
 * CLAUDE.md records that tradeoff being made deliberately for the Cognito path
 * ("bounded now by one hour rather than by a thirty-day database session") and this
 * inherits it. A database session would be revocable and would need a table, a grant
 * and a read on every request. One hour is the mitigation.
 */

import { EncryptJWT, jwtDecrypt } from "jose";

import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";

/** DISTINCT FROM `agentorg_session`. The two verifiers must never share input. */
export const GITHUB_SESSION_COOKIE = "agentorg_gh";

/** Matches `cognito.SESSION_MAX_AGE_SECONDS`, so neither kind outlives the other. */
export const MAX_AGE_SECONDS = 3600;

const REGION = process.env.AWS_REGION ?? "us-east-1";
const KEY_SECRET_NAME =
  process.env.WEB_SESSION_KEY_SECRET_NAME ?? "theagentorg-shared-web-session-key";
export const WIRE_SIGNING_KEY = "signing_key";

const ISSUER = "theagentorg.web";
const AUDIENCE = "theagentorg.session";

export class SessionRefused extends Error {
  readonly detail: string;
  constructor(message: string, detail = "") {
    super(message);
    this.name = "SessionRefused";
    this.detail = detail;
  }
}

/** What a GitHub session asserts. NO email, NO name — see `session.ts` on `login`. */
export type GitHubSession = {
  login: string;
  tenantId: string;
  accessToken: string;
};

let cachedKey: Uint8Array | undefined;

/**
 * The 32-byte content-encryption key.
 *
 * Cached per process because a Secrets Manager read on every request would add a
 * network round trip to every page load. The cache is keyed on nothing: there is one
 * key, and a rotation is picked up when the Lambda is replaced. That is a real
 * limitation and the reason it is acceptable is that rotating THIS key is a
 * sign-everybody-out operation by design.
 */
async function key(): Promise<Uint8Array> {
  if (cachedKey) return cachedKey;

  const sm = new SecretsManagerClient({ region: REGION });
  let raw: string | undefined;
  try {
    raw = (await sm.send(new GetSecretValueCommand({ SecretId: KEY_SECRET_NAME }))).SecretString;
  } catch (error) {
    // The NAME and the error TYPE. Never the value, and never the AWS message,
    // which can echo request context.
    throw new SessionRefused("sessions are not configured", `${KEY_SECRET_NAME}: ${(error as Error).name}`);
  }
  if (!raw) throw new SessionRefused("sessions are not configured", `${KEY_SECRET_NAME} is empty`);

  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    throw new SessionRefused("sessions are not configured", `${KEY_SECRET_NAME} is not JSON`);
  }
  const value = parsed[WIRE_SIGNING_KEY];
  if (typeof value !== "string" || !value) {
    throw new SessionRefused("sessions are not configured", `${KEY_SECRET_NAME} needs "${WIRE_SIGNING_KEY}"`);
  }

  // base64url -> bytes. A256GCM needs EXACTLY 32; a shorter key would otherwise be
  // padded or rejected deep inside `jose` with an error naming neither this secret
  // nor its length.
  const bytes = Buffer.from(value, "base64url");
  if (bytes.length !== 32) {
    throw new SessionRefused(
      "sessions are not configured",
      `${KEY_SECRET_NAME}.${WIRE_SIGNING_KEY} decodes to ${bytes.length} bytes, need 32`,
    );
  }
  cachedKey = new Uint8Array(bytes);
  return cachedKey;
}

/** Test seam: drop the cached key so a test can supply its own. */
export function _resetKeyCache(): void {
  cachedKey = undefined;
}

/** Encrypt a session into a cookie value. */
export async function mintSession(session: GitHubSession): Promise<string> {
  return await new EncryptJWT({
    login: session.login,
    tenant_id: session.tenantId,
    github_token: session.accessToken,
  })
    .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
    .setIssuedAt()
    .setIssuer(ISSUER)
    .setAudience(AUDIENCE)
    .setExpirationTime(`${MAX_AGE_SECONDS}s`)
    .encrypt(await key());
}

/**
 * A cookie value back into a session, or `null`.
 *
 * **`null` FOR EVERY FAILURE, exactly like `cognito.verifySession`.** Expired,
 * tampered, wrong audience, wrong issuer, unconfigured, malformed — one answer. A
 * partially-trusted session is a thing nobody can reason about, and a caller that
 * could tell the cases apart could tell an attacker which guess was closer.
 */
export async function readSession(cookie: string | undefined): Promise<GitHubSession | null> {
  if (!cookie) return null;
  try {
    const { payload } = await jwtDecrypt(cookie, await key(), {
      issuer: ISSUER,
      audience: AUDIENCE,
      // `contentEncryptionAlgorithms` pins A256GCM so a token announcing a weaker
      // one is refused rather than negotiated -- the same allowlist discipline the
      // Cognito verifier applies with `algorithms: ["RS256"]`.
      contentEncryptionAlgorithms: ["A256GCM"],
      keyManagementAlgorithms: ["dir"],
    });

    const login = payload.login;
    const tenantId = payload.tenant_id;
    const accessToken = payload.github_token;
    if (typeof login !== "string" || !login) return null;
    if (typeof tenantId !== "string" || !tenantId) return null;
    if (typeof accessToken !== "string" || !accessToken) return null;

    return { login, tenantId, accessToken };
  } catch {
    return null;
  }
}

/**
 * WHICH TENANT A GITHUB ACCOUNT ACTS IN.
 *
 * **DERIVED, NOT STORED, and that is a deliberate limitation.** A stored mapping
 * would need a table and a grant; a derived one needs neither and is stable across
 * restarts. The cost is that a tenant cannot be REASSIGNED without changing this
 * function — acceptable while one person owns the deployment, and the first thing to
 * revisit when a second organisation exists.
 *
 * **KEYED ON THE NUMERIC ID, NEVER THE LOGIN.** A GitHub login can be renamed and
 * then CLAIMED BY SOMEBODY ELSE; the numeric id is immutable. Deriving a tenant from
 * the login would hand a renamed account's entire workspace to whoever registered the
 * freed name — the worst failure this file could have, and it reads as correct code.
 *
 * The owner mapping is the bootstrap: without it the person who owns this deployment
 * signs in with GitHub and lands in an empty workspace, unable to see the runs they
 * have been making all week, which reads as the migration having lost everything.
 * `GITHUB_OWNER_LOGIN` is compared case-insensitively because GitHub logins are.
 */
export function tenantForGitHub(login: string, githubId: number): string {
  const owner = (process.env.GITHUB_OWNER_LOGIN ?? "mohamedsorour1998").trim().toLowerCase();
  if (owner && login.trim().toLowerCase() === owner) {
    return "tenant-zero";
  }
  return `t-gh-${githubId}`;
}
