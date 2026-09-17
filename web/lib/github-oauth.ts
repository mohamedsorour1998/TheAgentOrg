/**
 * SIGN IN WITH GITHUB — the identity half. **A GITHUB APP, NOT AN OAUTH APP.**
 *
 * The distinction is not cosmetic and it changed the design after the app existed.
 * CLAUDE.md records that per-repository scope "needs a GitHub App (installation
 * tokens, a different authorisation model)" and treats that as a cost. The operator
 * had already registered one (App ID `4677455`), so the expensive option was the one
 * we had — and it is the better one:
 *
 * | | OAuth App | **GitHub App (ours)** |
 * |---|---|---|
 * | repository scope | `repo` is ALL-OR-NOTHING — every repo the account can see | only repos the app was INSTALLED on |
 * | `scope` at authorize | required | **rejected** — permissions live on the App |
 * | the field to fill | "Authorization callback URL" | **"Redirect URI"** |
 *
 * That middle row is the one that breaks a copied implementation: sending `scope=repo`
 * to a GitHub App's authorize endpoint is not honoured, and reasoning about it as
 * though it were an OAuth App produces a token whose reach is a different set from the
 * one the code believes it asked for. **We ask for nothing here; the installation
 * decides.**
 *
 * ## What this module holds, and what it must never hold
 *
 * The client SECRET is read from Secrets Manager per call and never logged, never
 * returned, and never placed on a response — `lib/signup.ts`'s rule, for the same
 * reason. It is the one value that turns an authorization code into a token.
 *
 * **THE FIRST SECRET FOR THIS APP WAS DISCLOSED IN PLAINTEXT AND MUST BE TREATED AS
 * COMPROMISED**, which is why this file names no secret VALUE anywhere, only the
 * Secrets Manager id to read one from. A credential that has been pasted into a
 * transcript is revoked, not reused.
 *
 * ## The `state` parameter is a CSRF defence and is checked, not merely sent
 *
 * `authorizeUrl` mints it, the caller puts it in an `HttpOnly` cookie, and the
 * callback compares. A flow that SENDS state and never compares it looks identical in
 * a browser and defends nothing — the shape this repository already found in an
 * existence oracle that matched a message while the status code still differed.
 */

import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";

const REGION = process.env.AWS_REGION ?? "us-east-1";

/**
 * ONE SPELLING, and it is the id of a secret rather than a secret.
 * `{"client_id": "...", "client_secret": "..."}` — the same two-key shape
 * `lib/signup.ts` reads, whose cross-language key mismatch (`client_id` written,
 * `clientId` read) is already recorded as a measured defect.
 */
export const OAUTH_SECRET_NAME =
  process.env.GITHUB_OAUTH_SECRET_NAME ?? "theagentorg-shared-github-oauth";

export const WIRE_CLIENT_ID = "client_id";
export const WIRE_CLIENT_SECRET = "client_secret";

/** GitHub's endpoints. Constants so a typo is one place, not four. */
const AUTHORIZE = "https://github.com/login/oauth/authorize";
const TOKEN = "https://github.com/login/oauth/access_token";
const API = "https://api.github.com";

export class GitHubAuthRefused extends Error {
  readonly detail: string;
  constructor(message: string, detail = "") {
    super(message);
    this.name = "GitHubAuthRefused";
    this.detail = detail;
  }
}

/** The redirect URI, which must match the App's "Redirect URI" field EXACTLY. */
export function redirectUri(): string {
  const base = process.env.AUTH_URL;
  if (!base) {
    // No default. A guessed origin here produces a redirect_uri GitHub refuses,
    // and the error surfaces on GitHub's own page as though the app were broken.
    throw new GitHubAuthRefused(
      "GitHub sign-in is not configured",
      "AUTH_URL is not set, so the redirect URI cannot be derived",
    );
  }
  return `${base.replace(/\/+$/, "")}/api/auth/github/callback`;
}

/**
 * Where to send somebody to sign in, and the `state` the callback must see back.
 *
 * **NO `scope` PARAMETER.** A GitHub App's permissions are configured on the App and
 * granted at INSTALLATION; passing `scope` here is an OAuth App habit that this
 * endpoint does not honour. Sending one would read as a narrower or wider grant than
 * the token actually carries.
 */
export async function authorizeUrl(state: string): Promise<string> {
  const { clientId } = await credentials();
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri(),
    state,
  });
  return `${AUTHORIZE}?${params.toString()}`;
}

/** Read the App's credentials. The secret never leaves this module. */
async function credentials(): Promise<{ clientId: string; clientSecret: string }> {
  const sm = new SecretsManagerClient({ region: REGION });
  let raw: string | undefined;
  try {
    raw = (await sm.send(new GetSecretValueCommand({ SecretId: OAUTH_SECRET_NAME })))
      .SecretString;
  } catch (error) {
    // The NAME and the error TYPE, never the value, and never the underlying
    // message -- a Secrets Manager error can echo request context.
    throw new GitHubAuthRefused(
      "GitHub sign-in is not configured",
      `${OAUTH_SECRET_NAME}: ${(error as Error).name}`,
    );
  }
  if (!raw) {
    throw new GitHubAuthRefused("GitHub sign-in is not configured", `${OAUTH_SECRET_NAME} is empty`);
  }

  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    throw new GitHubAuthRefused(
      "GitHub sign-in is not configured",
      `${OAUTH_SECRET_NAME} is not JSON`,
    );
  }

  const clientId = parsed[WIRE_CLIENT_ID];
  const clientSecret = parsed[WIRE_CLIENT_SECRET];
  if (typeof clientId !== "string" || !clientId || typeof clientSecret !== "string" || !clientSecret) {
    // NAMES THE MISSING KEY, not the document. A misspelled key must fail loudly
    // here rather than fall through to a request GitHub answers with a generic
    // error -- CLAUDE.md records exactly that mismatch costing a debugging session.
    throw new GitHubAuthRefused(
      "GitHub sign-in is not configured",
      `${OAUTH_SECRET_NAME} needs string "${WIRE_CLIENT_ID}" and "${WIRE_CLIENT_SECRET}"`,
    );
  }
  return { clientId, clientSecret };
}

/** What a successful sign-in produces. The token is a CREDENTIAL — never rendered. */
export type GitHubIdentity = {
  login: string;
  /** GitHub's immutable numeric id. Stable across a rename; `login` is not. */
  id: number;
  accessToken: string;
  /** Present only when the App issues expiring user tokens. */
  refreshToken: string;
};

/**
 * Exchange the authorization code for a user-to-server token, then read the user.
 *
 * **GITHUB ANSWERS 200 ON FAILURE.** `POST /login/oauth/access_token` returns HTTP
 * 200 with `{"error": "bad_verification_code", ...}` for a code that is wrong,
 * expired or already used. A `response.ok` check therefore passes and the caller
 * reads `access_token` as `undefined` — the reassuring non-answer this repository
 * refuses. The body is what decides, not the status.
 */
export async function exchangeCode(code: string): Promise<GitHubIdentity> {
  const { clientId, clientSecret } = await credentials();

  const response = await fetch(TOKEN, {
    method: "POST",
    headers: {
      // Without this GitHub replies in x-www-form-urlencoded and `json()` throws.
      accept: "application/json",
      "content-type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({
      client_id: clientId,
      client_secret: clientSecret,
      code,
      redirect_uri: redirectUri(),
    }).toString(),
  });

  const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (typeof body.error === "string") {
    // `error_description` is GitHub's own prose and is safe to surface; the code
    // itself is not echoed, because it is a single-use credential.
    throw new GitHubAuthRefused("GitHub refused the sign-in", String(body.error));
  }
  const accessToken = body.access_token;
  if (typeof accessToken !== "string" || !accessToken) {
    throw new GitHubAuthRefused("GitHub refused the sign-in", "no access token in the reply");
  }

  const user = await fetch(`${API}/user`, {
    headers: {
      authorization: `Bearer ${accessToken}`,
      accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28",
    },
  });
  if (!user.ok) {
    throw new GitHubAuthRefused("GitHub refused the sign-in", `GET /user answered ${user.status}`);
  }
  const profile = (await user.json()) as { login?: unknown; id?: unknown };
  if (typeof profile.login !== "string" || !profile.login || typeof profile.id !== "number") {
    throw new GitHubAuthRefused("GitHub refused the sign-in", "GET /user returned no login");
  }

  return {
    login: profile.login,
    id: profile.id,
    accessToken,
    refreshToken: typeof body.refresh_token === "string" ? body.refresh_token : "",
  };
}

/**
 * The repositories this person's installations cover — bug #2's data.
 *
 * **THIS IS WHY THE GITHUB APP IS THE RIGHT ANSWER.** An OAuth App's `repo` scope
 * would return every repository the account can see, and the dropdown would be a
 * list of everything the person has access to anywhere. Here the set is decided by
 * where the App was INSTALLED, which is a choice the person made deliberately and
 * can revoke per repository.
 *
 * An EMPTY list is a real answer and must not be rendered as a failure: it means the
 * App is not installed on anything yet, and the fix is an install rather than a
 * retry. The caller says so.
 */
export async function installationRepositories(accessToken: string): Promise<string[]> {
  const headers = {
    authorization: `Bearer ${accessToken}`,
    accept: "application/vnd.github+json",
    "x-github-api-version": "2022-11-28",
  };

  const installations = await fetch(`${API}/user/installations?per_page=100`, { headers });
  if (!installations.ok) {
    throw new GitHubAuthRefused(
      "could not list your GitHub installations",
      `GET /user/installations answered ${installations.status}`,
    );
  }
  const list = (await installations.json()) as { installations?: { id?: unknown }[] };

  const names: string[] = [];
  for (const installation of list.installations ?? []) {
    if (typeof installation.id !== "number") continue;
    const repos = await fetch(
      `${API}/user/installations/${installation.id}/repositories?per_page=100`,
      { headers },
    );
    if (!repos.ok) continue;
    const page = (await repos.json()) as { repositories?: { full_name?: unknown }[] };
    for (const repo of page.repositories ?? []) {
      if (typeof repo.full_name === "string") names.push(repo.full_name);
    }
  }
  // Sorted and de-duplicated: two installations can cover one repository, and a
  // dropdown listing it twice reads as a bug in the product.
  return [...new Set(names)].sort();
}
