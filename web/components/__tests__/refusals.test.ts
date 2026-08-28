/**
 * WHAT THE SCREENS STRUCTURALLY CANNOT DO.
 *
 * Three refusals that hold today by inspection, pinned here so they keep holding.
 * Each is a case where the code is correct and one careless edit makes it wrong
 * while every screen still renders perfectly -- which is exactly the class of
 * defect this repository exists to prevent.
 *
 * READS SOURCE TEXT, and that is a compromise worth naming. A test over the AST
 * would be stronger and these files are ~40% commentary, so a naive `includes`
 * check is satisfied by the COMMENT explaining the thing it checks -- this
 * repository has found that twice. So every assertion below strips comments FIRST,
 * and `stripComments` has its own test proving it still strips. Without that
 * guard these would be the third instance.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const WEB = process.cwd();

/**
 * Remove `//` and block comments, and string-literal contents.
 *
 * String bodies go too: a prose sentence inside a rendered `"..."` is copy, not
 * code, and `"No request body carries a tenant_id"` on screen would otherwise
 * satisfy a search for `tenant_id`. Quote characters are kept so the shape of the
 * code survives.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/\/\/[^\n]*/g, " ")
    .replace(/"(?:[^"\\\n]|\\.)*"/g, '""')
    .replace(/'(?:[^'\\\n]|\\.)*'/g, "''")
    .replace(/`(?:[^`\\]|\\.)*`/g, "``");
}

function read(...parts: string[]): string {
  return stripComments(readFileSync(join(WEB, ...parts), "utf8"));
}

describe("the comment stripper", () => {
  // If this breaks, every assertion below starts passing for the wrong reason.
  it("removes both comment forms and string bodies", () => {
    const stripped = stripComments(
      ['/* tenant_id in a block */', "// tenant_id in a line", 'const a = "tenant_id";', "const b = real_tenant_id;"].join(
        "\n",
      ),
    );
    expect(stripped).not.toContain("tenant_id in a block");
    expect(stripped).not.toContain("tenant_id in a line");
    // The string BODY is gone...
    expect(stripped).toContain('""');
    // ...but a real identifier survives, so the checks below can still see code.
    expect(stripped).toContain("real_tenant_id");
  });
});

describe("the gate controls", () => {
  const source = read("components", "GateControls.tsx");

  it("is not empty after stripping, so these checks can see code", () => {
    expect(source).toContain("sendJson");
    expect(source.length).toBeGreaterThan(200);
  });

  it("cannot send `overridden`", () => {
    // `overridden` may be COMPARED for display (`d.decision === "overridden"`),
    // and after stripping, string bodies are gone -- so any surviving occurrence
    // would be an identifier or a bare token, which is what a send would need.
    expect(source).not.toContain("overridden");
  });

  it("cannot send a `by`, so a caller cannot attribute a decision to somebody else", () => {
    // NARROWED AFTER A FIRST VERSION FAILED, and the failure was the test's fault
    // rather than the code's -- worth recording, because the fix is the point.
    //
    // `/\bby\s*:/` matched `by: string` in `DecisionLog`'s PROPS TYPE, which reads
    // a `by` the server wrote. Reading it is required: it is how a screen shows
    // who approved. Only SENDING one is the hazard, so the assertion is about the
    // request body and not about the identifier.
    //
    // A test that forbids reading `by` would have forced the display of an
    // attribution to be deleted in order to go green -- a test actively making the
    // product worse, which this repository has seen once already.
    const body = source.match(/const body\s*:\s*ApprovalRequest\s*=\s*\{[^}]*\}/s);
    expect(body, "no ApprovalRequest body literal found; this test would pin nothing")
      .not.toBeNull();
    expect(body?.[0]).not.toMatch(/\bby\b/);
  });

  it("cannot send a tenant", () => {
    expect(source).not.toContain("tenant_id");
  });

  it("posts to the approvals route and nowhere else", () => {
    const calls = source.match(/sendJson</g) ?? [];
    expect(calls.length, "no sendJson call found; this test would pin nothing").toBe(1);
  });
});

describe("the stage spine", () => {
  // COMMENT-STRIPPED BUT NOT STRING-STRIPPED, and that distinction is the whole
  // reason this reads a second form of the file.
  //
  // MEASURED: the first version of this suite asserted over `read(...)`, whose
  // `stripComments` blanks string BODIES -- so `"var(--border)"` became `""`
  // before the check ran, the assertion could not see the token it forbids, and
  // the RED step came back INERT: 31 passed both with and without the defect.
  // The helper protecting these tests from their own commentary erased the
  // evidence. That is this repository's named pattern (a helper that cannot
  // express the failing case) reaching the test that was written to avoid it.
  //
  // A CSS custom property only ever appears inside a string, so any test about
  // one must keep strings. Comments still go, because the prose below explains
  // `--border` at length and would satisfy the check on its own.
  const withStrings = readFileSync(
    join(WEB, "components", "StageSpine.tsx"),
    "utf8",
  )
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/\/\/[^\n]*/g, " ");
  const source = read("components", "StageSpine.tsx");

  it("is not empty after stripping", () => {
    expect(source).toContain("PHASE_TEXT");
    // And the string-preserving form must still contain a token, or the
    // assertion below cannot fail.
    expect(withStrings).toContain("var(--");
  });

  /**
   * MEASURED, and the reason this test exists rather than a comment.
   *
   * `PHASE_COLOUR` holds `--border-strong` and `--border` for the two inactive
   * phases. Against `--surface` those measure 1.67:1 and 1.31:1 -- correct for a
   * 2px ring, illegible as a sentence. The first version of this component used
   * one table for both, so on a POISONED run every stage after the block rendered
   * "did not run" at 1.31:1: the words that exist so the spine does not read as
   * blank were the ones nobody could read, on the demo's central beat.
   *
   * A border token and a text token are not interchangeable. Two tables is what
   * stops the next edit collapsing them, and this asserts the collapse did not
   * happen.
   */
  it("colours the phase word from PHASE_TEXT, never from the mark's border tokens", () => {
    const textTable = withStrings.match(/PHASE_TEXT[^=]*=\s*\{[^}]*\}/s);
    expect(textTable, "PHASE_TEXT not found; this test would pin nothing").not.toBeNull();
    // A border token as text is the defect. `--border` also matches
    // `--border-strong`, so one check covers both.
    expect(textTable?.[0]).not.toContain("--border");
  });

  it("does not use the mark's colour variable as a text colour", () => {
    // The rendered word must read from the table, not from the `colour` binding
    // that draws the ring.
    expect(source).toMatch(/color:\s*PHASE_TEXT\[phase\]/);
    expect(source).not.toMatch(/color:\s*colour\b/);
  });
});

describe("the security panel", () => {
  const source = read("components", "SecurityPanel.tsx");

  it("is not empty after stripping", () => {
    expect(source).toContain("findings");
  });

  it("builds no link or anchor on a finding's line number", () => {
    // `Finding.line` is the index of an ADDED line, not a file position, so a
    // jump-to-line affordance would send a reader somewhere wrong with total
    // confidence. It may be rendered and used as a key; it must not be a target.
    const anchors = source.match(/href=/g) ?? [];
    expect(anchors.length).toBe(0);
    expect(source).not.toMatch(/#L\$\{|#L"/);
  });
});

describe("every screen", () => {
  const files = [
    ["components", "RunList.tsx"],
    ["components", "SecurityPanel.tsx"],
    ["components", "GateControls.tsx"],
    ["components", "CostPanel.tsx"],
    ["components", "StageSpine.tsx"],
    ["components", "AccountPanel.tsx"],
    ["components", "RepositoryPicker.tsx"],
    ["components", "SignInPanel.tsx"],
    ["components", "primitives.tsx"],
  ];

  it("declares no colour of its own -- the palette lives in globals.css", () => {
    // Five authors wrote these in parallel. A hex here is a second declaration of
    // a colour Lane I owns, and the two copies drift silently: what is on screen
    // stops being what was signed off. CLAUDE.md records the same hazard between
    // the deck generator and its HTML preview.
    expect(files.length).toBeGreaterThan(5);
    for (const parts of files) {
      const source = read(...parts);
      const hexes = source.match(/#[0-9a-fA-F]{3,8}\b/g) ?? [];
      expect(hexes, `${parts.join("/")} hardcodes ${hexes.join(", ")}`).toEqual([]);
    }
  });
});
