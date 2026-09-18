/**
 * STARTING A RUN AGAINST A REPOSITORY THIS TENANT DOES NOT OWN MUST BE REFUSED.
 *
 * The pipeline gained a `repo` workflow input so a run is no longer stuck on
 * whichever repository the `DEMO_REPO` variable named — asked for from the deployed
 * app: *"when creating an issue we should be able to select which repo"*.
 *
 * **THE INPUT AUTHORISES NOTHING, AND THIS ROUTE IS THE ONLY THING THAT BOUNDS IT.**
 * The dispatch token can already write to every repository the GitHub App was
 * installed on, so a value in the request cannot widen what is reachable — what
 * decides is whether this route refuses it, and whether it refuses BEFORE the token
 * is read. A check on the far side of a credential is a check the credential has
 * already got past.
 *
 * **THE ASSERTION THAT MATTERS IS `startRun` NEVER BEING CALLED.** A 403 with the
 * issue already created would be a refusal reported after the side effect: an issue
 * opened on somebody else's repository, and a run this tenant could never approve,
 * because `web/lib/authz.ts` refuses an approval whose run is outside the scope.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const currentIdentity = vi.fn();
const readPipeline = vi.fn();
const startRun = vi.fn();

vi.mock("@/lib/session", () => ({ currentIdentity }));
vi.mock("@/lib/pipeline", () => ({ readPipeline }));
vi.mock("@/lib/dispatch", () => ({
  startRun,
  // The real class, so the route's `instanceof` branch is reachable.
  DispatchRefused: class DispatchRefused extends Error {},
}));

const IN_SCOPE = ["acme/auth-service", "acme/billing"];

function post(body: Record<string, unknown>): Request {
  return new Request("https://example.test/api/runs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

beforeEach(() => {
  currentIdentity.mockReset();
  readPipeline.mockReset();
  startRun.mockReset();
  currentIdentity.mockResolvedValue({ login: "a-real-person", tenantId: "t1" });
  readPipeline.mockResolvedValue({
    repositories: IN_SCOPE.map((full_name) => ({ full_name })),
  });
  startRun.mockResolvedValue({ issue: "61" });
});

describe("POST /api/runs — which repository", () => {
  it("REFUSES one outside this tenant's scope, and starts nothing", async () => {
    const { POST } = await import("../app/api/runs/route");
    const response = await POST(post({ title: "Add a rate limit to the login endpoint", repository: "someone-else/private" }));

    expect(response.status).toBe(403);
    // THE WHOLE POINT. A refusal after the issue was created is a refusal reported
    // after the damage: an issue on somebody else's repository, and a run this
    // tenant could never approve.
    expect(startRun, "the run was dispatched despite the refusal").not.toHaveBeenCalled();
  });

  it("names what IS in scope, so a stale list is not read as a typo", async () => {
    const { POST } = await import("../app/api/runs/route");
    const body = (await (
      await POST(post({ title: "Add a rate limit to the login endpoint", repository: "nope/nope" }))
    ).json()) as { error: string; detail?: string };

    expect(body.error).toContain("nope/nope");
    expect(body.detail).toContain("acme/auth-service");
  });

  it("passes an in-scope choice through unchanged", async () => {
    const { POST } = await import("../app/api/runs/route");
    const response = await POST(
      post({ title: "Add a rate limit to the login endpoint", repository: "acme/billing" }),
    );

    expect(response.status).toBe(202);
    expect(startRun).toHaveBeenCalledWith(expect.objectContaining({ repository: "acme/billing" }));
  });

  it("falls back to the tenant's first repository when none is named", async () => {
    // WHAT EVERY CALLER GOT BEFORE THIS CONTROL EXISTED. A blank field submits "",
    // which must read as "did not choose" rather than as a refusal.
    const { POST } = await import("../app/api/runs/route");
    await POST(post({ title: "Add a rate limit to the login endpoint", repository: "  " }));

    expect(startRun).toHaveBeenCalledWith(
      expect.objectContaining({ repository: "acme/auth-service" }),
    );
  });

  it("still refuses a tenant with an empty scope, before reading any credential", async () => {
    // AN EMPTY SCOPE IS A REFUSAL, NOT AN EXEMPTION — the same direction as Lane K's
    // empty key store. Written the other way, a tenant that had connected nothing
    // could run against the deployment's default repository.
    readPipeline.mockResolvedValue({ repositories: [] });
    const { POST } = await import("../app/api/runs/route");
    const response = await POST(post({ title: "Add a rate limit to the login endpoint" }));

    expect(response.status).toBe(409);
    expect(startRun).not.toHaveBeenCalled();
  });

  it("refuses an unauthenticated caller before it reads the scope at all", async () => {
    currentIdentity.mockResolvedValue(null);
    const { POST } = await import("../app/api/runs/route");
    const response = await POST(post({ title: "Add a rate limit to the login endpoint" }));

    expect(response.status).toBe(401);
    // ORDERING, not just the status: an anonymous caller must not be able to drive
    // a tenancy read from this deployment's address. `infra/ingress/handler.py` puts
    // its cheap rejections before the secret fetch for the same reason.
    expect(readPipeline).not.toHaveBeenCalled();
    expect(startRun).not.toHaveBeenCalled();
  });
});
