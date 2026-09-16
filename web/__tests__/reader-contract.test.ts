/**
 * Every field the contract DECLARES must be present in what the reader RETURNS.
 *
 * **THIS EXISTS BECAUSE THREE SHAPES WERE INCOMPLETE AND ONLY ONE CRASHED.** The
 * DynamoDB reader was written by reading the Python it replaced, and the Python
 * built its answers from a live `RunState`. Three fields were quietly dropped:
 *
 *     RunDetail.awaiting_gates   -> `runs/[runId]/page.tsx:192` reads `.length`
 *     CostView.findings          -> `CostPanel.tsx:166` reads `.length`
 *     ScoringResponse.scan_provenance
 *
 * The first one shipped. Measured against the deployed app, signed in:
 *
 *     /api/runs/<id>          200, valid JSON
 *     /api/runs/<id>/cost     200, valid JSON
 *     /api/runs/<id>/scoring  200, valid JSON
 *     /runs/<id>              "This page couldn't load"
 *     console: Uncaught TypeError: Cannot read properties of undefined (reading 'length')
 *
 * **NOTHING SERVER-SIDE LOOKED WRONG**, which is the whole difficulty: an omitted
 * key is `undefined`, `JSON.stringify` drops it silently, every route answered 200,
 * and the failure surfaced in React as a blank error screen. `tsc` could not catch
 * it either — the reader's return type is inferred and flows into `readPipeline<T>`
 * through an unchecked cast, so the compiler was never asked to compare the two.
 *
 * So this compares them at RUNTIME: it parses the declared field names out of the
 * contract, calls the reader against a stubbed DynamoDB, and asserts each one is
 * present. The interfaces are TypeScript types and are erased at runtime, which is
 * exactly why the source is read as text rather than imported.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

const REPO_ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..", "..");

/** Stub the credential so no STS or DynamoDB call is made. */
const send = vi.fn();
vi.mock("../lib/dynamo/credentials", () => ({
  scopedClient: async () => ({ send }),
  TAG_KEY: "tenant",
}));

const CONTRACTS = [
  readFileSync(path.join(REPO_ROOT, "web", "lib", "contract.ts"), "utf8"),
  readFileSync(path.join(REPO_ROOT, "web", "lib", "endpoints.ts"), "utf8"),
].join("\n");

/**
 * The field names one interface or type alias declares, INCLUDING those it inherits
 * through `extends`. `RunDetail extends RunSummary` and the page reads fields from
 * both, so stopping at the child would let half the shape go unchecked.
 */
function declaredFields(name: string, seen = new Set<string>()): string[] {
  if (seen.has(name)) return [];
  seen.add(name);
  const block = new RegExp(
    `export (?:interface|type) ${name}(?:\\s+extends\\s+([A-Za-z]+))?\\s*=?\\s*\\{([\\s\\S]*?)\\n\\}`,
  ).exec(CONTRACTS);
  if (!block) return [];
  const inherited = block[1] ? declaredFields(block[1], seen) : [];
  // Only OPTIONAL fields may be absent; a `?` means the contract permits omission.
  const own = [...(block[2] ?? "").matchAll(/^ {2}([a-z_]+)(\??):/gm)]
    .filter((m) => m[2] !== "?")
    .map((m) => m[1] as string);
  return [...inherited, ...own];
}

beforeEach(() => {
  send.mockReset();
  process.env.TENANCY_TABLE = "theagentorg-tenancy-test";
  process.env.TENANT_SCOPED_ROLE_ARN = "arn:aws:iam::339712964409:role/fake";
});

async function read(request: Record<string, unknown>): Promise<Record<string, unknown>> {
  const { readTenancy } = await import("../lib/dynamo/reader");
  return (await readTenancy(request)) as Record<string, unknown>;
}

describe("the reader returns every field the contract declares", () => {
  it("parses the contracts at all", () => {
    // Anti-vacuity. Every case below compares against a parsed list, and an empty
    // list would make each one pass by checking nothing.
    expect(declaredFields("RunSummary").length).toBeGreaterThan(4);
    expect(declaredFields("RunDetail")).toContain("awaiting_gates");
    expect(declaredFields("CostView")).toContain("findings");
  });

  it("RunDetail — the shape whose gap reached the deployed app", async () => {
    send.mockResolvedValue({ Item: { run_id: "r1", ticket_id: "59", status: "running" } });
    const answer = await read({ action: "run_detail", tenant_id: "t1", run_id: "r1" });

    for (const field of declaredFields("RunDetail")) {
      expect(
        Object.hasOwn(answer, field),
        `run_detail omits \`${field}\`, which RunDetail declares as required. An ` +
          `omitted key is undefined, JSON.stringify drops it, the route still ` +
          `answers 200 — and the screen dies in React.`,
      ).toBe(true);
    }
  });

  it("CostView", async () => {
    send.mockResolvedValue({ Item: { run_id: "r1" } });
    const answer = await read({ action: "run_cost", tenant_id: "t1", run_id: "r1" });
    for (const field of declaredFields("CostView")) {
      expect(Object.hasOwn(answer, field), `run_cost omits \`${field}\``).toBe(true);
    }
  });

  it("ScoringResponse", async () => {
    send.mockResolvedValue({ Item: { run_id: "r1" } });
    const answer = await read({ action: "run_scoring", tenant_id: "t1", run_id: "r1" });
    for (const field of declaredFields("ScoringResponse")) {
      expect(Object.hasOwn(answer, field), `run_scoring omits \`${field}\``).toBe(true);
    }
  });

  it("RunSummary, for every row the list returns", async () => {
    send.mockResolvedValue({
      Items: [{ run_id: "r1", ticket_id: "59", status: "running", created_at: "2026-09-15" }],
    });
    const answer = await read({ action: "list_runs", tenant_id: "t1" });
    const rows = answer.runs as Record<string, unknown>[];
    expect(rows).toHaveLength(1);
    for (const field of declaredFields("RunSummary")) {
      expect(Object.hasOwn(rows[0]!, field), `list_runs rows omit \`${field}\``).toBe(true);
    }
  });

  it("returns NOTHING the contract does not declare", async () => {
    // The mirror of the checks above, and it caught a real one: `run_cost` returned
    // `total_usd`, which `CostView` has never had. A field nobody declared is a
    // field nobody renders — dead weight that reads as data.
    send.mockResolvedValue({ Item: { run_id: "r1" } });
    const answer = await read({ action: "run_cost", tenant_id: "t1", run_id: "r1" });
    const declared = new Set([...declaredFields("CostView"), "run_id"]);
    for (const key of Object.keys(answer)) {
      expect(declared.has(key), `run_cost returns \`${key}\`, which CostView does not declare`).toBe(
        true,
      );
    }
  });
});
