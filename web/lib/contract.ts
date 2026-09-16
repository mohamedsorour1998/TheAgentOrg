/**
 * THE ENDPOINT CONTRACT. Lane I publishes it; Lane J imports it.
 *
 * Spec `docs/final/01-specification.md` §11 (judge requirement 9), plan §4 LANE I.
 *
 * WHY THIS FILE EXISTS BEFORE ANY BEHAVIOUR DOES
 * ==============================================
 * Lane J owns `web/app/(routes)/**` and `web/components/**` and cannot start until
 * the shapes are named. Two lanes each inventing a `Run` type is two declarations
 * of one fact, free to drift, and the drift is silent -- the screen renders
 * `run.verdict` while the route answers `security.verdict` and nothing is red until
 * a judge is watching. So the types land first, in one commit, and every route in
 * `web/app/api/**` is typed against THIS file rather than against its own idea.
 *
 * TYPES ONLY. No `import` of anything runtime, no `pg`, no `next/server`, no
 * `next-auth`. A UI component that imports a server-only module gets a build error
 * that names the wrong thing, and Lane J will import this from client components.
 * `import type { ... } from "@/lib/contract"` must be free.
 *
 * THE VOCABULARY IS THE PYTHON CONTRACT'S, RESTATED IN TYPESCRIPT
 * ==============================================================
 * `agentorg/state.py` is FROZEN and is the source of truth for every union below.
 * TypeScript cannot import a pydantic Literal, so these are a second declaration --
 * the one kind this repository accepts, for `test_scoring_determinism.py`'s reason:
 * a second declaration is the only way to detect a change in the first. The drift
 * is closed by a test rather than a promise -- `web/lib/__tests__/contract.test.ts`
 * reads `agentorg/state.py` and asserts each union matches its Literal member for
 * member, and asserts its own matcher found something before comparing.
 *
 * WHAT IS DELIBERATELY ABSENT: any field a client could send that would change a
 * security verdict, a threshold, or a tenant. Every route derives the tenant from
 * the session on the server. There is no `tenant_id` in ANY request body in this
 * file, and `web/lib/__tests__/contract.test.ts` asserts that structurally -- Lane
 * K's `test_no_route_takes_a_tenant_from_the_request` for the same reason: a tenant
 * a caller can name is a tenant a caller can choose.
 */

// ── the frozen vocabulary, mirrored from agentorg/state.py ────────────────────

/** `state.py:22`. Ordered low → critical by `SEVERITY_ORDER`. */
export type Severity = "low" | "medium" | "high" | "critical";

/** `state.py:24`. The nine stages a run passes through. */
export type Stage =
  | "plan"
  | "gate1"
  | "develop"
  | "review"
  | "security"
  | "gate2"
  | "sre"
  | "gate3"
  | "promote";

/** `state.py:307`. A run's own status, not a job's. */
export type RunStatus =
  | "running"
  | "blocked"
  | "rejected"
  | "promoted"
  | "failed";

/**
 * `queue/__init__.py:96`. A JOB's status, which is NOT a run's.
 *
 * The four terminal non-`done` values are deliberately not collapsed: `blocked`
 * is exit 3, the deterministic rule WORKING, and a UI that painted it the same
 * as `failed` would show the demo's central beat as a crash.
 */
export type JobStatus =
  | "ready"
  | "claimed"
  | "paused"
  | "done"
  | "blocked"
  | "rejected"
  | "already_final"
  | "failed";

/** `state.py:282`. The three gates, and nothing else is one. */
export type Gate = "gate1" | "gate2" | "gate3";

/**
 * `state.py:283`. What a human may record.
 *
 * `overridden` is IN the type and REFUSED by the route -- see `ApprovalRequest`.
 * Naming it here is not an oversight: Lane J renders a run's history, and a
 * decision recorded by `gates_cli resume --decision overridden` is a real row it
 * must be able to display. A union that could not express it would make the CLI's
 * override render as a corrupt record.
 */
export type Decision = "approved" | "rejected" | "overridden";

/**
 * `state.py:49`. Where a security verdict came from.
 *
 * `""` is the fourth, unnameable state -- a row written before the field existed.
 * Lane J must render it as *unknown* and never as a scan. Collapsing it into
 * `scanners` would make an unmeasured run read as a measured one, which is the
 * defect the field exists to prevent.
 */
export type ScanProvenance =
  | "scanners"
  | "fixture-fallback"
  | "fixture-stub"
  | "";

/** `state.py:95`. The three scanners, exactly. */
export type ScannerTool = "semgrep" | "gitleaks" | "trivy";

// ── the read models ──────────────────────────────────────────────────────────

/**
 * One row of a run list. Deliberately NARROW -- the list view must not need a
 * `RunState` per row, because reading one is a file read or a GetItem each.
 *
 * `verdict` is `null` when security has not run, NOT `"pass"`. A default of
 * `"pass"` would paint a run that has not been scanned as one that was cleared,
 * which is this project's signature defect shape.
 */
export interface RunSummary {
  run_id: string;
  ticket_id: string;
  status: RunStatus;
  created_at: string;
  /** `null` until the security stage has produced one. */
  verdict: "pass" | "block" | null;
  /** `""` means nobody recorded provenance -- render as unknown. */
  scan_provenance: ScanProvenance;
  /** How many findings were at or above the threshold. `null` if not scanned. */
  blocking: number | null;
  /** The gate this run is paused at, or `""` if it is not paused. */
  awaiting_gate: Gate | "";
}

/** One finding, as `state.py:94` declares it. */
export interface Finding {
  tool: ScannerTool;
  severity: Severity;
  rule: string;
  file: string;
  /**
   * THE INDEX OF AN ADDED LINE, NOT A FILE POSITION. CLAUDE.md is explicit: a
   * finding at `app/auth.py:3` means the THIRD ADDED LINE. Do not build a
   * jump-to-line affordance on this, and do not label it "line 3 of the file".
   */
  line: number;
  description: string;
}

/**
 * One row of Lane C's scoring artifact -- `state.py:103`.
 *
 * `native` is the scanner's own word, unmapped, and `""` when the scanner emits
 * no severity at all (gitleaks). Rendering only `mapped` would hide the mapping
 * this table exists to make auditable.
 */
export interface ScoreRow {
  tool: ScannerTool;
  rule: string;
  native: string;
  mapped: Severity;
  threshold: Severity;
  blocking: boolean;
}

/** The security verdict as a screen needs it. */
export interface SecurityView {
  verdict: "pass" | "block";
  findings: Finding[];
  blocking: Finding[];
  explanation: string;
  scan_provenance: ScanProvenance;
  scoring: ScoreRow[];
}

/** One stage of a run, as the queue saw it. Drives the live run view. */
export interface StageView {
  stage: string;
  status: JobStatus;
  attempt: number;
  exit_code: number | null;
  enqueued_at: string;
  updated_at: string;
  /**
   * Non-empty means the job was reclaimed from a worker whose lease expired, so
   * THE STAGE MAY HAVE RUN TWICE. The only trace of at-least-once delivery;
   * surface it rather than hiding it.
   */
  reclaimed_from: string;
}

/** A human decision already on the record. `state.py:281`. */
export interface DecisionView {
  gate: Gate;
  decision: Decision;
  by: string;
  at: string;
  reason: string;
}

/** Everything one run's detail screen needs, in one response. */
/**
 * WHAT EACH AGENT PRODUCED, mirroring `agentorg/state.py`'s result models.
 *
 * These are VIEWS and every field is optional, which is the deliberate part: the
 * Python side is the frozen contract and may ADD optional fields at any time, so a
 * required field here would make this half refuse a run the other half considers
 * valid. `SecurityView` and `DecisionView` above already take that shape.
 *
 * `pr_url` is on `DevView` because `github_ops` fills it, not the agent -- the same
 * note `state.py` carries.
 */
export interface PlanView {
  tasks?: string[];
  acceptance_criteria?: string[];
  target_files?: string[];
  notes?: string;
}

export interface DevView {
  branch?: string;
  diff?: string;
  summary?: string;
  files_changed?: string[];
  pr_url?: string;
}

export interface ReviewView {
  verdict?: "approve" | "changes_requested";
  comments?: { file?: string; line?: number; comment?: string }[];
  must_fix?: string[];
}

export interface SREView {
  verdict?: "go" | "no_go";
  /** MEASURED on the runner, not the model's opinion -- see `agents/sre.py`. */
  ci_status?: "passing" | "failing" | "unknown";
  slo_checks?: { name?: string; status?: string; detail?: string }[];
  estimated_cost_note?: string;
  notes?: string;
}

export interface RunDetail extends RunSummary {
  ticket_text: string;
  /** `""` on a run written before the field existed -- render as unknown. */
  model_provenance: string;
  trigger: string;
  poisoned: boolean;
  pr_url: string | null;
  branch: string | null;
  stages: StageView[];
  decisions: DecisionView[];
  security: SecurityView | null;
  /** Which gates are open for a decision RIGHT NOW. May be empty. */
  awaiting_gates: Gate[];

  /**
   * `null` MEANS THE STAGE HAS NOT RUN, and an empty object would mean it ran and
   * produced nothing. Collapsing the two tells somebody the reviewer had no
   * objections when the reviewer never ran -- the did-not-run-versus-passed
   * conflation this repository exists to refuse.
   */
  plan: PlanView | null;
  dev: DevView | null;
  review: ReviewView | null;
  sre: SREView | null;
  /** Both are `null` on every run today: neither producer is wired into a stage. */
  generated_tests: Record<string, unknown> | null;
  retrieval: Record<string, unknown> | null;
}
