/**
 * THE CONTRACT IS A SECOND DECLARATION, AND THIS IS WHAT MAKES THAT ACCEPTABLE.
 *
 * `web/lib/contract.ts` mirrors unions from `agentorg/state.py`, which is the FROZEN
 * contract. TypeScript cannot import a pydantic `Literal`, so a copy is unavoidable —
 * and this repository's rule about copies is explicit: a second declaration is only
 * acceptable when something compares them. `test_scoring_determinism.py` restates the
 * severity ranking as a literal for exactly this reason, "because a second declaration
 * is the only way to detect a change in the first".
 *
 * WITHOUT THIS FILE the failure is silent and one-directional: a member ADDED to
 * `state.py` leaves the UI unable to render a real value, and a member REMOVED leaves
 * the UI rendering one that can no longer occur. Neither breaks a build. Both would
 * ship.
 *
 * EVERY MATCHER ASSERTS IT MATCHED. A regex against Python source that stops matching
 * — because a declaration was reformatted, or moved — would make every comparison here
 * vacuously true while the drift it exists to catch went through. CLAUDE.md records the
 * exact instance: a Lane C RED step built its mutation from CLAUDE.md's text rather
 * than the source, the substitution matched nothing, "the suite stayed green at
 * `25 passed`, and an inert mutation is indistinguishable from a caught one".
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const REPO_ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "..",
  "..",
);

const STATE_PY = readFileSync(path.join(REPO_ROOT, "agentorg", "state.py"), "utf8");
const CONTRACT_TS = readFileSync(
  path.join(REPO_ROOT, "web", "lib", "contract.ts"),
  "utf8",
);
const QUEUE_PY = readFileSync(
  path.join(REPO_ROOT, "agentorg", "queue", "__init__.py"),
  "utf8",
);

/**
 * The members of a Python `Literal[...]`, given the name it is assigned to.
 *
 * Handles a multi-line Literal, because `Stage` and `JobStatus` are both wrapped. The
 * caller asserts the result is non-empty — this function returning `[]` for a
 * declaration it could not find is exactly the vacuous case.
 */
function pythonLiteral(source: string, name: string): string[] {
  // Non-greedy up to the closing bracket, `[\s\S]` so newlines are crossed.
  const match = source.match(new RegExp(`${name}\\s*=\\s*Literal\\[([\\s\\S]*?)\\]`));
  if (match === null) return [];
  return [...(match[1] ?? "").matchAll(/"([^"]+)"/g)].map((m) => m[1] as string);
}

/** The members of a TypeScript string-union type alias. */
function tsUnion(source: string, name: string): string[] {
  const match = source.match(new RegExp(`export type ${name} =([\\s\\S]*?);`));
  if (match === null) return [];
  return [...(match[1] ?? "").matchAll(/"([^"]*)"/g)].map((m) => m[1] as string);
}

/**
 * The members of a Python `Literal` inside a pydantic field declaration.
 *
 * ANCHORED TO A LINE START PLUS INDENTATION, and that anchor is a MEASURED fix rather
 * than caution. The first version matched `${field}\s*:` anywhere, so asking for
 * `status` matched **`ci_status`** on `SREResult` — a different field, earlier in the
 * file — and the test failed with:
 *
 *     expected [ 'blocked', 'failed', …(3) ] to deeply equal
 *              [ 'failing', 'passing', 'unknown' ]
 *
 * It failed loudly, which is the only reason it was caught. Had `ci_status` happened
 * to carry the same members, the comparison would have passed against the wrong
 * declaration — a matcher that matches the wrong thing is worse than one that matches
 * nothing, because nothing is detectable and wrong is not.
 *
 * `\n    ` requires exactly one level of class-body indentation, which every pydantic
 * field in `state.py` has and no substring of a longer name can satisfy.
 */
function pythonFieldLiteral(source: string, field: string): string[] {
  const match = source.match(
    new RegExp(`\\n    ${field}\\s*:\\s*Literal\\[([\\s\\S]*?)\\]`),
  );
  if (match === null) return [];
  return [...(match[1] ?? "").matchAll(/"([^"]+)"/g)].map((m) => m[1] as string);
}

describe("the matchers work, so every comparison below is real", () => {
  it.each([
    ["Severity", 4],
    ["Stage", 9],
    ["ScanProvenance", 3],
  ])("finds %s in state.py with %i members", (name, count) => {
    const found = pythonLiteral(STATE_PY, name);
    expect(
      found.length,
      `${name} was not found in state.py; every comparison against it would pin nothing`,
    ).toBe(count);
  });

  it("finds JobStatus in the queue with 8 members", () => {
    expect(pythonLiteral(QUEUE_PY, "JobStatus").length).toBe(8);
  });

  it("finds the TypeScript unions it compares against", () => {
    for (const name of ["Severity", "Stage", "ScanProvenance", "JobStatus", "RunStatus", "Gate", "Decision"]) {
      expect(
        tsUnion(CONTRACT_TS, name).length,
        `${name} was not found in contract.ts`,
      ).toBeGreaterThan(0);
    }
  });
});

describe("the vocabulary matches agentorg/state.py, member for member", () => {
  it("Severity", () => {
    expect(tsUnion(CONTRACT_TS, "Severity").sort()).toEqual(
      pythonLiteral(STATE_PY, "Severity").sort(),
    );
  });

  it("Stage — all nine", () => {
    expect(tsUnion(CONTRACT_TS, "Stage").sort()).toEqual(
      pythonLiteral(STATE_PY, "Stage").sort(),
    );
  });

  it("ScanProvenance, PLUS the empty string the Python union adds separately", () => {
    // `ScanProvenanceOrUnknown = ScanProvenance | Literal[""]` in Python — two
    // declarations there, one union here. So the comparison adds `""` explicitly
    // rather than pretending the shapes are identical.
    const python = [...pythonLiteral(STATE_PY, "ScanProvenance"), ""];
    expect(tsUnion(CONTRACT_TS, "ScanProvenance").sort()).toEqual(python.sort());
  });

  it("RunStatus — from RunState.status, not a standalone alias", () => {
    expect(tsUnion(CONTRACT_TS, "RunStatus").sort()).toEqual(
      pythonFieldLiteral(STATE_PY, "status").sort(),
    );
  });

  it("Gate — from HumanDecision.gate", () => {
    const python = pythonFieldLiteral(STATE_PY, "gate");
    expect(python.length, "HumanDecision.gate was not found").toBe(3);
    expect(tsUnion(CONTRACT_TS, "Gate").sort()).toEqual(python.sort());
  });

  it("Decision — from HumanDecision.decision, INCLUDING overridden", () => {
    // `overridden` MUST be in the type even though the approvals route refuses it:
    // Lane J renders a run's history, and a decision recorded by `gates_cli resume
    // --decision overridden` is a real row it has to display. A union that could not
    // express it would make the CLI's override render as a corrupt record.
    const python = pythonFieldLiteral(STATE_PY, "decision");
    expect(python).toContain("overridden");
    expect(tsUnion(CONTRACT_TS, "Decision").sort()).toEqual(python.sort());
  });

  it("JobStatus — from the QUEUE, and it is NOT the same as RunStatus", () => {
    expect(tsUnion(CONTRACT_TS, "JobStatus").sort()).toEqual(
      pythonLiteral(QUEUE_PY, "JobStatus").sort(),
    );
    // ASSERTED AS DIFFERENT, deliberately. A job's status and a run's status share
    // three words (`blocked`, `rejected`, `failed`) and mean different things, and the
    // queue's four terminal values are "deliberately NOT collapsed into one `done`"
    // because `blocked` is exit 3, the pipeline WORKING. A UI that painted the two
    // vocabularies identically would show the demo's central beat as a crash.
    expect(tsUnion(CONTRACT_TS, "JobStatus")).not.toEqual(
      tsUnion(CONTRACT_TS, "RunStatus"),
    );
  });

  it("ScannerTool — the three scanners, exactly", () => {
    const python = pythonFieldLiteral(STATE_PY, "tool");
    expect(python.sort()).toEqual(["gitleaks", "semgrep", "trivy"]);
    expect(tsUnion(CONTRACT_TS, "ScannerTool").sort()).toEqual(python.sort());
  });
});
