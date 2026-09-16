/**
 * The detail screen must show what a run actually did.
 *
 * **THE DEFECT THIS PINS WAS REPORTED FROM THE DEPLOYED APP.** `/runs/<id>` rendered
 * every stage as `NOT STARTED`, `STARTED BY unknown`, `AGENTS ANSWERED FROM not
 * recorded`, `NOT SCANNED`, and no cost — for a run whose `plan` job had *completed
 * successfully*. Saying a stage did not run when it did is the exact
 * did-not-run-versus-passed conflation this repository exists to refuse, rendered on
 * a screen.
 *
 * The cause was that the run's own record lives in a GitHub Actions artifact, which
 * an Amplify SSR Lambda cannot read. `agentorg/tenancy/run_index.py` now writes a
 * copy onto the index row at every stage, and these assert the reader derives the
 * screen from it.
 *
 * **NOTHING HERE IS INVENTED, AND THAT IS THE PART WORTH GUARDING.** `StageView`
 * carries `attempt`, `exit_code` and `enqueued_at`, which are QUEUE facts — and a run
 * on the Actions path never enters the queue. A fabricated `exit_code: 0` would be
 * the one field on this screen capable of contradicting the run itself.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const send = vi.fn();
vi.mock("../lib/dynamo/credentials", () => ({
  scopedClient: async () => ({ send }),
  TAG_KEY: "tenant",
}));

/** A run that planned, developed, was scanned and blocked — the poisoned beat. */
const BLOCKED_STATE = {
  run_id: "r1",
  ticket_id: "43",
  ticket_text: "Add a per-IP login rate limit.",
  started_at: "2026-09-16T03:54:55Z",
  status: "blocked",
  trigger: "ui",
  model_provenance: "model",
  poisoned: true,
  plan: { tasks: ["a"] },
  dev: { branch: "feat/x", pr_url: "https://github.com/x/y/pull/44" },
  review: { verdict: "changes_requested" },
  security: {
    verdict: "block",
    findings: [{ tool: "gitleaks" }, { tool: "gitleaks" }],
    blocking: [{ tool: "gitleaks" }, { tool: "gitleaks" }],
    scan_provenance: "scanners",
    scoring: [{ threshold: "high" }],
  },
  sre: null,
  decisions: [{ gate: "gate1", decision: "approved", by: "a-person", at: "", reason: "" }],
  cost: { usd: 0.0131, stages: [{ stage: "plan" }, { stage: "develop" }], cache_hit_rate: null, findings: [] },
};

function rowWith(state: unknown) {
  return {
    Item: {
      run_id: "r1",
      ticket_id: "43",
      status: "blocked",
      created_at: "2026-09-16T03:54:55Z",
      ...(state === undefined ? {} : { state: JSON.stringify(state) }),
    },
  };
}

async function detail(): Promise<Record<string, unknown>> {
  const { readTenancy } = await import("../lib/dynamo/reader");
  return (await readTenancy({
    action: "run_detail",
    tenant_id: "t1",
    run_id: "r1",
  })) as Record<string, unknown>;
}

beforeEach(() => {
  send.mockReset();
  process.env.TENANCY_TABLE = "t";
  process.env.TENANT_SCOPED_ROLE_ARN = "arn:aws:iam::339712964409:role/fake";
});

describe("the detail screen reflects the run", () => {
  it("marks the stages that ran as done, and omits the ones that did not", async () => {
    send.mockResolvedValue(rowWith(BLOCKED_STATE));
    const answer = await detail();
    const stages = answer.stages as { stage: string; status: string }[];
    const byName = Object.fromEntries(stages.map((s) => [s.stage, s.status]));

    // Ran, and proven by a field on the run.
    expect(byName.plan).toBe("done");
    expect(byName.gate1).toBe("done");
    expect(byName.develop).toBe("done");
    expect(byName.review).toBe("done");
    expect(byName.security).toBe("done");

    // A BLOCKED RUN IS WAITING FOR NOBODY. `gate2` must not be listed as paused —
    // the run ended at the security verdict, and a gate marked "your decision" on a
    // run that already refused would invite a click that cannot exist.
    expect(byName.gate2).toBeUndefined();
    expect(byName.promote).toBeUndefined();
  });

  it("invents no queue facts", async () => {
    send.mockResolvedValue(rowWith(BLOCKED_STATE));
    const stages = (await detail()).stages as Record<string, unknown>[];
    for (const stage of stages) {
      // `exit_code` is the dangerous one: a fabricated 0 would be the single field
      // here capable of contradicting the run it describes.
      expect(stage.exit_code, `${stage.stage} invented an exit code`).toBeNull();
      expect(stage.reclaimed_from).toBe("");
    }
  });

  it("carries the security verdict, the provenance and the blocking count", async () => {
    send.mockResolvedValue(rowWith(BLOCKED_STATE));
    const answer = await detail();
    expect(answer.verdict).toBe("block");
    // `scanners` vs `fixture-fallback` is the distinction the whole verification
    // story rests on; a screen that showed `""` for a real scan would erase it.
    expect(answer.scan_provenance).toBe("scanners");
    expect(answer.blocking).toBe(2);
    expect(answer.trigger).toBe("ui");
    expect(answer.model_provenance).toBe("model");
    expect(answer.poisoned).toBe(true);
    expect(answer.pr_url).toBe("https://github.com/x/y/pull/44");
  });

  it("a run with no document shows blanks rather than claiming anything", async () => {
    // The honest degraded case: an index row written before the read model existed.
    // It must not assert `verdict: pass` or a stage list it cannot know.
    send.mockResolvedValue(rowWith(undefined));
    const answer = await detail();
    expect(answer.stages).toEqual([]);
    expect(answer.verdict).toBeNull();
    expect(answer.scan_provenance).toBe("");
    expect(answer.blocking).toBeNull();
  });

  it("a malformed document degrades instead of losing the whole screen", async () => {
    send.mockResolvedValue({
      Item: { run_id: "r1", ticket_id: "43", status: "running", state: "{not json" },
    });
    const answer = await detail();
    expect(answer.run_id).toBe("r1");
    expect(answer.stages).toEqual([]);
  });

  it("a live run lists the gate it is waiting at", async () => {
    send.mockResolvedValue(
      rowWith({ ...BLOCKED_STATE, status: "running", security: null, review: null, dev: null, decisions: [] }),
    );
    const stages = (await detail()).stages as { stage: string; status: string }[];
    const byName = Object.fromEntries(stages.map((s) => [s.stage, s.status]));
    expect(byName.plan).toBe("done");
    // THE POINT OF THE SCREEN for a run in flight: somebody is being waited on.
    expect(byName.gate1).toBe("paused");
  });

  it("the cost rows come from the document", async () => {
    send.mockResolvedValue(rowWith(BLOCKED_STATE));
    const { readTenancy } = await import("../lib/dynamo/reader");
    const cost = (await readTenancy({
      action: "run_cost",
      tenant_id: "t1",
      run_id: "r1",
    })) as Record<string, unknown>;
    // `stages_priced` and not `usd` is how "is cost wired?" is answered — Lane E
    // measured that an unwired run has zero rows with usd null, while a run that
    // fell back has a row per stage with usd 0.0.
    expect(cost.stages_priced).toBe(2);
    expect(cost.usd).toBe(0.0131);
  });

  /**
   * **THE SCREEN SHOWED A STATUS AND A SPINE AND NOTHING EITHER AGENT PRODUCED.**
   * Reported from the deployed app: *"i dont understand how it is running and we
   * have no info"*. Every field below was already on the index row, because
   * `run_index` denormalises the whole state document — the screen was holding this
   * data and not projecting it.
   */
  it("carries what each agent produced", async () => {
    send.mockResolvedValue(rowWith(BLOCKED_STATE));
    const answer = await detail();
    expect((answer.plan as { tasks: string[] }).tasks).toEqual(["a"]);
    expect((answer.dev as { branch: string }).branch).toBe("feat/x");
    expect((answer.review as { verdict: string }).verdict).toBe("changes_requested");
  });

  it("a stage that has not run is null, never an empty result", async () => {
    // `sre` is null on this run. `{}` would say the SRE ran and advised nothing —
    // the did-not-run-versus-passed conflation, in the direction that reads as good
    // news. `AgentOutput` renders null as "has not run" and cannot do that unless
    // the reader keeps the two apart.
    send.mockResolvedValue(rowWith(BLOCKED_STATE));
    const answer = await detail();
    expect(answer.sre).toBeNull();
  });

  /**
   * **THIS FIELD WAS HARDCODED `[]`, WHICH MADE THE APPROVE BUTTON UNREACHABLE.**
   * `runs/[runId]/page.tsx:192` renders `GateControls` only when
   * `awaiting_gates.length > 0`, so no run ever showed one — and the approval route,
   * its authorization, its dispatch to `pending_deployments` and every test over the
   * three were reached by nothing. The API was fixed while the UI still could not
   * call it.
   */
  it("names the gate a live run is held at, so the control can be shown", async () => {
    send.mockResolvedValue(
      rowWith({ ...BLOCKED_STATE, status: "running", security: null, review: null, dev: null, decisions: [] }),
    );
    expect((await detail()).awaiting_gates).toEqual(["gate1"]);
  });

  it("a run that has ended offers no gate on the screen either", async () => {
    // The screen and the decision must agree. Offering a control the server would
    // then refuse is worse than offering none.
    send.mockResolvedValue(rowWith(BLOCKED_STATE));
    expect((await detail()).awaiting_gates).toEqual([]);
  });
});

describe("run_facts — the contract an approval is decided over", () => {
  /**
   * **A DIFFERENT SHAPE FROM `run_detail`, AND CONFLATING THEM BROKE APPROVALS.**
   * Measured against the deployed app: `POST /api/approvals` answered
   * `404 {"error": "no such run. Nothing was recorded."}` for a run plainly waiting
   * at gate1, because `runFacts` compares `payload.tenant_id !== tenantId` as
   * defence in depth and the detail shape carries no `tenant_id` — so the check was
   * `undefined !== "tenant-zero"` and refused every approval.
   *
   * It failed in the SAFE direction, which is exactly why it was invisible.
   */
  it("carries every field authz.decide reads", async () => {
    send.mockResolvedValue(rowWith({ ...BLOCKED_STATE, status: "running", security: null, decisions: [] }));
    const { readTenancy } = await import("../lib/dynamo/reader");
    const facts = (await readTenancy({
      action: "run_facts",
      tenant_id: "tenant-zero",
      run_id: "r1",
    })) as Record<string, unknown>;

    for (const field of ["run_id", "tenant_id", "repository_full_name", "status", "awaiting_gates"]) {
      expect(Object.hasOwn(facts, field), `run_facts omits \`${field}\``).toBe(true);
    }
  });

  it("reports the tenant from the ROW, not the argument", async () => {
    /**
     * **A FIRST VERSION OF THIS ASSERTION WAS INERT, AND IT IS RECORDED RATHER THAN
     * QUIETLY FIXED.** It called `run_facts` with `tenant-zero` against a fixture row
     * carrying no `tenant_id`, so `row.tenant_id ?? tenantId` answered the argument
     * either way — replacing the whole expression with `tenantId` changed nothing and
     * the suite stayed at 246 passed. An inert mutation reads exactly like a caught
     * one.
     *
     * The failing case needs the two to DIFFER, which is precisely the case the
     * defence-in-depth check in `approvals.runFacts` exists for: if the store ever
     * returned a row belonging to somebody else, echoing the argument would hide it
     * and the comparison would pass.
     */
    send.mockResolvedValue({
      Item: {
        run_id: "r1",
        ticket_id: "43",
        status: "running",
        tenant_id: "somebody-else",
        state: JSON.stringify({ ...BLOCKED_STATE, status: "running" }),
      },
    });
    const { readTenancy } = await import("../lib/dynamo/reader");
    const facts = (await readTenancy({
      action: "run_facts", tenant_id: "tenant-zero", run_id: "r1",
    })) as Record<string, unknown>;

    expect(
      facts.tenant_id,
      "run_facts echoed the caller's tenant instead of the row's, so approvals.runFacts " +
        "would compare a string to itself and the cross-tenant check would never fire",
    ).toBe("somebody-else");
  });

  it("names the gate a live run is held at", async () => {
    send.mockResolvedValue(rowWith({ ...BLOCKED_STATE, status: "running", security: null, decisions: [] }));
    const { readTenancy } = await import("../lib/dynamo/reader");
    const facts = (await readTenancy({
      action: "run_facts", tenant_id: "t1", run_id: "r1",
    })) as Record<string, unknown>;
    // `plan` is done and gate1 undecided, so gate1 is open. A gate absent from this
    // list may not be decided, whatever the reason.
    expect(facts.awaiting_gates).toEqual(["gate1"]);
  });

  it("a run that has ENDED is awaiting nobody", async () => {
    send.mockResolvedValue(rowWith(BLOCKED_STATE));
    const { readTenancy } = await import("../lib/dynamo/reader");
    const facts = (await readTenancy({
      action: "run_facts", tenant_id: "t1", run_id: "r1",
    })) as Record<string, unknown>;
    // A blocked run must not offer a gate: `authz.decide` refuses a terminal run,
    // and offering one would invite a click that cannot exist.
    expect(facts.awaiting_gates).toEqual([]);
    expect(facts.status).toBe("blocked");
  });

  it("refuses the scope check when the tenant has no single repository", async () => {
    // `""` FAILS `authz.decide`'s scope check, which is the point: a guess would
    // permit an approval against a repository nobody named. RunState carries no
    // repository field, so one connected repo is the only honest answer.
    send.mockImplementation((cmd: unknown) => {
      const input = (cmd as { input?: Record<string, unknown> }).input ?? {};
      if ("Key" in input) return Promise.resolve(rowWith({ ...BLOCKED_STATE, status: "running" }));
      return Promise.resolve({ Items: [] });        // no repositories in scope
    });
    const { readTenancy } = await import("../lib/dynamo/reader");
    const facts = (await readTenancy({
      action: "run_facts", tenant_id: "t1", run_id: "r1",
    })) as Record<string, unknown>;
    expect(facts.repository_full_name).toBe("");
  });
});
