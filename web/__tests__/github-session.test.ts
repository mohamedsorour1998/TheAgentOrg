/**
 * THE GITHUB SESSION — the tenant it derives, and what it refuses.
 *
 * `tenantForGitHub` decides **whose runs an account reads**. It is four lines and
 * it is the most dangerous function added for GitHub sign-in, because the wrong
 * version of it reads as obviously correct.
 */

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
