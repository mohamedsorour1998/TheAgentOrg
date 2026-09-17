/**
 * THE GITHUB SESSION — the tenant it derives, and what it refuses.
 *
 * `tenantForGitHub` decides **whose runs an account reads**. It is four lines and
 * it is the most dangerous function added for GitHub sign-in, because the wrong
 * version of it reads as obviously correct.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const send = vi.fn();
vi.mock("@aws-sdk/client-secrets-manager", () => ({
  SecretsManagerClient: class {
    send = send;
  },
  GetSecretValueCommand: class {
    constructor(public input: unknown) {}
  },
}));

/** 32 bytes, base64url — the length A256GCM requires. FAKE, and test-only. */
const FAKE_KEY = Buffer.alloc(32, 7).toString("base64url");

beforeEach(async () => {
  send.mockReset();
  send.mockResolvedValue({ SecretString: JSON.stringify({ signing_key: FAKE_KEY }) });
  const { _resetKeyCache } = await import("../lib/github-session");
  _resetKeyCache();
  delete process.env.GITHUB_OWNER_LOGIN;
});

afterEach(() => {
  delete process.env.GITHUB_OWNER_LOGIN;
});

describe("which tenant a GitHub account acts in", () => {
  it("keys on the NUMERIC ID, never the login", async () => {
    /**
     * **THE FAILING CASE THIS EXISTS FOR.** A GitHub login can be renamed, and the
     * freed name can then be REGISTERED BY SOMEBODY ELSE. A tenant derived from the
     * login would hand the renamed account's entire workspace — every run, every
     * repository, every gate decision — to whoever claimed the old name. It reads
     * as correct code, and there is no test the wrong version fails unless the test
     * varies the login and the id independently.
     */
    const { tenantForGitHub } = await import("../lib/github-session");

    // Same person, renamed. The tenant must NOT move.
    expect(tenantForGitHub("old-name", 4242)).toBe(tenantForGitHub("new-name", 4242));

    // A DIFFERENT person who has taken the freed login. Must NOT inherit anything.
    expect(tenantForGitHub("old-name", 9999)).not.toBe(tenantForGitHub("old-name", 4242));
  });

  it("gives every account its own tenant", async () => {
    const { tenantForGitHub } = await import("../lib/github-session");
    // An empty-but-configured workspace, which is the honest answer and the one
    // that demonstrates the isolation rather than bypassing it.
    expect(tenantForGitHub("someone", 1)).toBe("t-gh-1");
    expect(tenantForGitHub("someone-else", 2)).toBe("t-gh-2");
  });

  it("maps the deployment owner onto tenant zero", async () => {
    process.env.GITHUB_OWNER_LOGIN = "mohamedsorour1998";
    const { tenantForGitHub } = await import("../lib/github-session");
    // THE BOOTSTRAP. Without it the person who owns this deployment signs in with
    // GitHub and lands in an empty workspace, unable to see the runs they have been
    // making all week -- which reads as the migration having lost everything.
    expect(tenantForGitHub("mohamedsorour1998", 111)).toBe("tenant-zero");
    // GitHub logins are case-insensitive, so the comparison must be too.
    expect(tenantForGitHub("MohamedSorour1998", 111)).toBe("tenant-zero");
    // And nobody else gets it.
    expect(tenantForGitHub("someone", 112)).toBe("t-gh-112");
  });
});

describe("the origin a redirect is built from", () => {
  /**
   * **THE SIGN-IN COMPLETED AND SENT THE BROWSER TO `https://localhost:3000/runs`.**
   * Reported from the deployed app. Everything before the last line had worked —
   * the state matched, the code was exchanged, the session was minted and the
   * cookie set on the real domain — and then the person was sent to an address
   * their machine cannot reach. Safari says it cannot connect, which reads as the
   * whole sign-in being broken when only the final hop is.
   *
   * **THIS CANNOT BE REPRODUCED LOCALLY**, which is the whole reason it shipped:
   * with no proxy in front, `request.nextUrl.origin` IS the public origin and
   * every redirect is correct. Behind Amplify the handler runs in a Lambda behind
   * CloudFront and receives the internal origin. `app/api/auth/logout/route.ts`
   * already read `AUTH_URL` for exactly this reason; the GitHub routes were
   * written without carrying it across.
   */
  it("prefers AUTH_URL over the request's own origin", async () => {
    const { appOrigin } = await import("../lib/github-oauth");
    process.env.AUTH_URL = "https://theagentorg.rosettacloud.app";

    // The internal origin the Lambda actually sees, which must NOT win.
    expect(appOrigin("https://localhost:3000")).toBe("https://theagentorg.rosettacloud.app");
    expect(new URL("/runs", appOrigin("https://localhost:3000")).toString()).toBe(
      "https://theagentorg.rosettacloud.app/runs",
    );
    delete process.env.AUTH_URL;
  });

  it("falls back to the request origin for the self-hosted stack", async () => {
    const { appOrigin } = await import("../lib/github-oauth");
    delete process.env.AUTH_URL;
    // No proxy there, so the request origin is the public one.
    expect(appOrigin("http://127.0.0.1:3000")).toBe("http://127.0.0.1:3000");
  });

  it("tolerates a trailing slash on AUTH_URL", async () => {
    const { appOrigin } = await import("../lib/github-oauth");
    process.env.AUTH_URL = "https://theagentorg.rosettacloud.app/";
    // `new URL("/runs", base)` is fine either way, but a doubled slash shows up in
    // logs and in the address bar, and somebody will file it as a bug.
    expect(appOrigin("x")).toBe("https://theagentorg.rosettacloud.app");
    delete process.env.AUTH_URL;
  });

  it("is what the routes actually build their redirects from", () => {
    /**
     * The tests above prove `appOrigin` WORKS. They cannot prove anything CALLS it,
     * and a correct helper nobody calls is this repository's second named pattern —
     * which is precisely the state the routes were in when the bug shipped.
     *
     * Asserted over comment-stripped source, because the docstrings in both routes
     * discuss `request.nextUrl.origin` at length and a bare substring check would
     * be satisfied by the prose explaining the hazard. That exact failure was
     * caught in this session on the sign-out link.
     */
    const strip = (source: string) =>
      source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

    for (const file of [
      "app/api/auth/github/route.ts",
      "app/api/auth/github/callback/route.ts",
    ]) {
      const code = strip(readFileSync(join(__dirname, "..", file), "utf8"));

      // ANTI-VACUITY: the stripper must leave the route behind.
      expect(code.includes("export async function GET"), `${file}: stripped to nothing`).toBe(true);

      expect(
        /new URL\([^)]*,\s*request\.nextUrl\.origin\s*\)/.test(code),
        `${file} builds a redirect from request.nextUrl.origin. Behind Amplify that is ` +
          `the Lambda's INTERNAL origin, so sign-in completes and sends the browser to ` +
          `https://localhost:3000/runs, which their machine cannot reach.`,
      ).toBe(false);

      expect(
        code.includes("appOrigin("),
        `${file} does not use appOrigin, so its redirects are not public-origin safe`,
      ).toBe(true);
    }
  });

  it("marks the session cookie Secure from the PUBLIC origin", async () => {
    const { originIsSecure } = await import("../lib/github-oauth");
    process.env.AUTH_URL = "https://theagentorg.rosettacloud.app";
    // Behind the proxy the internal request is plain http, so a flag derived from
    // it marks the cookie non-Secure on a site served entirely over https.
    // Browsers ACCEPT that, which is exactly why it would not have been noticed.
    expect(originIsSecure("http://localhost:3000")).toBe(true);
    delete process.env.AUTH_URL;
    expect(originIsSecure("http://127.0.0.1:3000")).toBe(false);
  });
});

describe("reading a session back", () => {
  it("round-trips a session it minted", async () => {
    const { mintSession, readSession } = await import("../lib/github-session");
    const cookie = await mintSession({
      login: "octocat",
      tenantId: "t-gh-583231",
      accessToken: "ghu_FAKE_TOKEN_FOR_TESTS",
    });

    // ENCRYPTED, NOT MERELY SIGNED. The cookie carries a GitHub credential, and a
    // signed JWT is base64 -- anyone holding the cookie could read the token out of
    // it and use it against GitHub directly, outliving this session.
    expect(cookie).not.toContain("octocat");
    expect(cookie).not.toContain("ghu_FAKE_TOKEN_FOR_TESTS");

    expect(await readSession(cookie)).toEqual({
      login: "octocat",
      tenantId: "t-gh-583231",
      accessToken: "ghu_FAKE_TOKEN_FOR_TESTS",
    });
  });

  it("answers null for every failure, and never throws", async () => {
    const { readSession } = await import("../lib/github-session");
    // One answer for absent, malformed and tampered -- `cognito.verifySession`'s
    // rule. A caller that could tell them apart could tell an attacker which guess
    // was closer, and a partially-trusted session is a thing nobody can reason about.
    for (const value of [undefined, "", "not-a-jwt", "a.b.c", "a.b.c.d.e"]) {
      expect(await readSession(value)).toBeNull();
    }
  });

  it("refuses a session minted under a different key", async () => {
    const { mintSession, readSession, _resetKeyCache } = await import("../lib/github-session");
    const cookie = await mintSession({
      login: "octocat",
      tenantId: "t-gh-1",
      accessToken: "ghu_FAKE",
    });

    // The attacker's key, not ours.
    _resetKeyCache();
    send.mockResolvedValue({
      SecretString: JSON.stringify({ signing_key: Buffer.alloc(32, 9).toString("base64url") }),
    });

    expect(await readSession(cookie)).toBeNull();
  });

  it("refuses a key that is not 32 bytes, naming the length", async () => {
    const { mintSession, _resetKeyCache } = await import("../lib/github-session");
    _resetKeyCache();
    send.mockResolvedValue({
      SecretString: JSON.stringify({ signing_key: Buffer.alloc(16, 1).toString("base64url") }),
    });
    // A256GCM needs exactly 32. Without this check the failure surfaces deep inside
    // `jose` naming neither the secret nor its length.
    //
    // ASSERTED ON `detail`, NOT ON `message`, and the split is the design rather
    // than an inconvenience: `message` is what a caller may surface ("sessions are
    // not configured") and is deliberately incapable of describing the
    // configuration, while `detail` is for the log. A first version of this test
    // asserted on `message` and failed -- correctly, against code that is right.
    const refusal = await mintSession({ login: "a", tenantId: "t", accessToken: "x" })
      .then(() => null)
      .catch((error: Error & { detail?: string }) => error);

    expect(refusal, "minting succeeded under a 16-byte key").not.toBeNull();
    expect(refusal?.message).toBe("sessions are not configured");
    expect(refusal?.detail, "the refusal does not name the actual length").toMatch(/16 bytes, need 32/);
  });
});
