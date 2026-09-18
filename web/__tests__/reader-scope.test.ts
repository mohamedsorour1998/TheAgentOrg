/**
 * SAVING THE SCOPE REPLACES IT. It used to only add.
 *
 * **REPORTED FROM THE DEPLOYED APP, TWICE.** A repository was unticked, `Save
 * scope` answered *"Scope saved."*, and it came back ticked. Then again after the
 * "no access" mark shipped — because that mark was a display fix for a write bug,
 * and the write was the thing that was wrong.
 *
 * `setScope` looped the wanted names, skipped the ones already present, wrote the
 * rest, and **never deleted a row for a repository that was no longer wanted**. So
 * unticking anything did nothing, for every repository, and the screen reported
 * success. It is the signature defect of this repository in a write path: a check
 * that cannot distinguish "did not happen" from "worked".
 *
 * **WHY NOTHING CAUGHT IT.** Every layer around the write called it a replace — a
 * `PUT` route, a screen saying "unticking a repository removes it", a request
 * carrying the whole set rather than a delta — and the tests asserted on what came
 * BACK, which is read from the table and was therefore honestly reporting a scope
 * that had not changed. The only place the contradiction was visible was the
 * write's own first line, which said "Add repositories to this tenant's scope".
 *
 * **THE SCOPE IS AN AUTHORISATION BOUNDARY**, which is what makes add-only the
 * dangerous direction: `authz.decide` permits an approval when the run's
 * repository is in this list, so a repository nobody can remove is a permission
 * nobody can revoke.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const send = vi.fn();
vi.mock("../lib/dynamo/credentials", () => ({
  scopedClient: async () => ({ send }),
  TAG_KEY: "tenant",
}));

/** Two repositories in scope, each keyed on a random id rather than its name. */
const ROWS = [
  { sk: "REPO#id-grace", full_name: "acme/Grace", tenant_id: "t1" },
  { sk: "REPO#id-auth", full_name: "acme/auth-service", tenant_id: "t1" },
];

/** What the fake table answers, and what it was asked to change. */
function harness() {
  const deleted: string[] = [];
  const put: string[] = [];

  send.mockImplementation((command: { constructor: { name: string }; input: Record<string, never> }) => {
    const kind = command.constructor.name;
    const input = command.input as Record<string, unknown>;
    if (kind === "QueryCommand") return Promise.resolve({ Items: ROWS });
    if (kind === "DeleteCommand") {
      deleted.push(String((input.Key as Record<string, unknown>).sk));
      return Promise.resolve({});
    }
    if (kind === "PutCommand") {
      put.push(String((input.Item as Record<string, unknown>).full_name));
      return Promise.resolve({});
    }
    return Promise.resolve({});
  });

  return { deleted, put };
}

beforeEach(() => {
  send.mockReset();
  process.env.TENANCY_TABLE = "t";
  process.env.TENANT_SCOPED_ROLE_ARN = "arn:aws:iam::339712964409:role/fake";
});

describe("saving the repository scope", () => {
  it("DELETES a repository that is no longer wanted", async () => {
    const { deleted } = harness();
    const { readTenancy } = await import("../lib/dynamo/reader");

    await readTenancy({
      action: "set_scope",
      tenant_id: "t1",
      full_names: ["acme/auth-service"],
      by: "a-real-person",
    });

    // THE WHOLE BUG IN ONE ASSERTION. Without the delete this list is empty, the
    // call still resolves, and the screen still says "Scope saved."
    expect(
      deleted,
      "unticking a repository sent no DeleteCommand, so the save reported success " +
        "and changed nothing -- which is how a permission becomes unrevokable",
    ).toEqual(["REPO#id-grace"]);
  });

  it("keys the delete on the ROW's own sort key, not on the name", async () => {
    const { deleted } = harness();
    const { readTenancy } = await import("../lib/dynamo/reader");

    await readTenancy({
      action: "set_scope",
      tenant_id: "t1",
      full_names: [],
      by: "a-real-person",
    });

    // A repository row is keyed on a random id, so the key CANNOT be rebuilt from
    // `full_name`. A delete addressed as `REPO#acme/Grace` targets a row that does
    // not exist -- and DynamoDB answers a delete of a missing item with success,
    // so the bug would survive the fix, silently, with this test still green if it
    // only counted the calls.
    expect(deleted.sort()).toEqual(["REPO#id-auth", "REPO#id-grace"]);
  });

  it("does not rewrite a repository that is already there", async () => {
    const { put } = harness();
    const { readTenancy } = await import("../lib/dynamo/reader");

    await readTenancy({
      action: "set_scope",
      tenant_id: "t1",
      full_names: ["acme/Grace", "acme/auth-service"],
      by: "a-real-person",
    });

    // Rewriting is not harmless: the row carries `created_at`, and a save that
    // touched nothing would reset when every repository was added.
    expect(put).toEqual([]);
  });

  it("still refuses a scope change with nobody's name on it", async () => {
    harness();
    const { readTenancy, ReadRefused } = await import("../lib/dynamo/reader");

    // An authorisation boundary changing anonymously is the same defect as a gate
    // decision with a constant `by`. The delete path must not have opened a way
    // round it.
    await expect(
      readTenancy({ action: "set_scope", tenant_id: "t1", full_names: [], by: "  " }),
    ).rejects.toBeInstanceOf(ReadRefused);
  });
});
