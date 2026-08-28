/**
 * THE ENDPOINT TABLE, and the request/response shape of each. Lane J imports this.
 *
 * Part two of the contract; `contract.ts` holds the vocabulary and the read models.
 * Split because that file is the one Lane J's client components import and this one
 * names routes -- keeping the union types free of route paths means a component
 * cannot accidentally depend on a URL.
 *
 * WHY A TABLE AND NOT A FOLDER LISTING
 * ====================================
 * Lane K's `openapi.py` declares `ROUTES` once and the dispatcher matches the same
 * tuple, so the document and the server cannot disagree. Same idea here: `ENDPOINTS`
 * below is the ONE declaration, `web/lib/__tests__/endpoints.test.ts` walks
 * `web/app/api/**` and asserts every `route.ts` on disk appears in this table and
 * every table entry has a file -- in BOTH directions, because a table naming a route
 * nobody built reads as a capability that exists, and a route absent from the table
 * is one Lane J will never call.
 *
 * That bidirectional check is the lesson from Lane K's absent gate scope: "a scope
 * nobody holds reads as a capability that exists, and the next person grants it and
 * hunts for the broken route."
 */

import type {
  Decision,
  Gate,
  RunDetail,
  RunSummary,
  ScanProvenance,
  ScoreRow,
  Severity,
} from "./contract";

// ── requests ─────────────────────────────────────────────────────────────────

/**
 * A gate decision, posted by a human with a session. THE DANGEROUS ONE.
 *
 * NO `by` FIELD, AND ITS ABSENCE IS THE WHOLE POINT. `agentorg/approve_server.py`
 * records `by="ui-reviewer"` for every decision because with no authentication it
 * "genuinely does not know who clicked". This surface does know: `by` comes from
 * the session on the server and a client cannot influence it. A `by` in this body
 * would let a caller attribute their own approval to somebody else, on the one
 * field whose entire purpose is attributing a decision to a person.
 *
 * NO `tenant_id` EITHER, for the same reason -- see `contract.ts`'s header.
 *
 * `decision` is NARROWER than `Decision`: `"overridden"` is refused by the route
 * with 422. Overriding a security block is the most dangerous thing the vocabulary
 * can express, and `approve_server.py` already made the same trade -- requiring
 * shell access (`gates_cli resume --decision overridden`) rather than a click. A
 * network endpoint is strictly weaker than a shell, so it must not widen it.
 */
export interface ApprovalRequest {
  run_id: string;
  gate: Gate;
  decision: "approved" | "rejected";
  reason?: string;
}

/** Choosing which repositories are in scope (I2). Names, never ids. */
export interface RepositoryScopeRequest {
  /** `owner/name`. Every one must be a repository the session's GitHub grant sees. */
  full_names: string[];
}

// ── responses ────────────────────────────────────────────────────────────────

/**
 * What EVERY error answers with. One shape, so Lane J writes one error component.
 *
 * `error` is a sentence for a human; `detail` is optional machine context. Neither
 * ever carries a value the caller supplied -- that is attacker-controlled text on a
 * rendered page, and `approve_server._one` already refuses to echo it.
 */
export interface ApiError {
  error: string;
  detail?: string;
}

/**
 * The answer to a gate decision. `recorded` is `false` on every refusal, and the
 * refusal reason is in `error`.
 *
 * `status` is the RUN's status after the decision, which is not always what the
 * decision implies: `gates.resume` sets `status="rejected"` for a rejection and
 * NEVER un-sets it, so approving a run the graph already rejected leaves it
 * `rejected`. This route refuses that case at the boundary (it is not an awaiting
 * gate), but the field is here so a screen shows the run's real state rather than
 * assuming an approval made it `running`.
 */
export interface ApprovalResponse {
  recorded: boolean;
  run_id: string;
  gate: Gate;
  decision: Decision;
  /** Who the SERVER attributed it to, read back so a screen can show it. */
  by: string;
  status: string;
}

/** Cost for one run (I6), reading Lane E. */
export interface CostView {
  /**
   * `null` means NOT PRICED -- an unknown model or a stale price table. `0.0`
   * means priced and free. Lane E's `CostRecord.usd` draws the same distinction
   * and collapsing them would make a missing price table look like a free run.
   */
  usd: number | null;
  /**
   * How many stage rows exist. THIS IS THE DISCRIMINATOR FOR "is cost wired?",
   * never `usd`: an unwired run has zero rows and `usd: null`; a wired run has a
   * row per stage even when that stage spent nothing.
   */
  stages_priced: number;
  stages: Array<{
    stage: string;
    model: string;
    input_tokens: number;
    output_tokens: number;
    cached_tokens: number;
    /**
     * Did the provider say anything about caching at all? `false` with zero
     * tokens means it reported no cache field; `true` with zero means it measured
     * zero. Both render "0.0%" and want different fixes.
     */
    cached_reported: boolean;
  }>;
  /**
   * `null` for a zero denominator, never `0.0`. And a rate that merely ROUNDS to
   * zero still carries the finding -- Lane E measured that `1e-06` renders
   * `0.0%` while comparing unequal to zero.
   */
  cache_hit_rate: number | null;
  /** Stated in words, because nobody reads a percentage as an alarm. */
  findings: string[];
}

/** One frame of the live event stream (I4). */
export interface RunEvent {
  /**
   * The cursor. An ISO-8601 UTC timestamp, which sorts lexicographically -- the
   * property the queue already relies on for lease comparison. A client resumes
   * with `?since=<cursor>`.
   */
  cursor: string;
  run_id: string;
  /** `stage` for a job transition, `log` for a decision-log row. */
  kind: "stage" | "log" | "heartbeat";
  stage: string;
  status: string;
  summary: string;
}

/** The session as a screen needs it. Never carries a token. */
export interface SessionView {
  signed_in: boolean;
  /** `null` when not signed in. The GitHub login, which is what a person recognises. */
  login: string | null;
  name: string | null;
  image: string | null;
  /** Which tenant the server resolved. A screen shows it; it cannot change it. */
  tenant_id: string | null;
  /** `true` once a GitHub grant is linked and not revoked. */
  github_linked: boolean;
}

/** One repository the session may act on (I2). */
export interface RepositoryView {
  full_name: string;
  /** In scope for this tenant, i.e. runs may be started against it. */
  in_scope: boolean;
}

// ── the table ────────────────────────────────────────────────────────────────

export interface Endpoint {
  method: "GET" | "POST" | "PUT" | "DELETE";
  /** The path as Next.js routes it. `[param]` segments are literal here. */
  path: string;
  /** What it does, in one line. */
  summary: string;
  /**
   * `true` when an unauthenticated caller is refused with 401. Every route is
   * authenticated except the two that structurally cannot be: the Auth.js handler
   * itself (it IS the sign-in) and the session read (whose whole answer is
   * "nobody is signed in").
   */
  authenticated: boolean;
  /**
   * `true` when the route MUTATES and therefore requires the CSRF defence. Every
   * mutating route is POST/PUT and checks `Origin`; a GET never mutates, which is
   * what makes POST-only meaningful (`approve_server.do_GET`'s note).
   */
  mutates: boolean;
}

/**
 * EVERY endpoint this lane ships. Both directions are asserted by a test.
 *
 * There is deliberately NO route that starts a run against an arbitrary
 * repository without a scope check, and NO route that takes a threshold -- a
 * client-chosen threshold is a client-chosen security verdict.
 */
export const ENDPOINTS: readonly Endpoint[] = [
  {
    method: "GET",
    path: "/api/auth/[...nextauth]",
    summary: "Auth.js: sign in, sign out, callback, CSRF token, session",
    authenticated: false,
    mutates: false,
  },
  {
    method: "POST",
    path: "/api/auth/[...nextauth]",
    summary: "Auth.js: the sign-in and sign-out POSTs, CSRF-protected by Auth.js",
    authenticated: false,
    mutates: true,
  },
  {
    method: "GET",
    path: "/api/session",
    summary: "Who is signed in, and which tenant the server resolved for them",
    authenticated: false,
    mutates: false,
  },
  {
    method: "GET",
    path: "/api/repositories",
    summary: "Repositories this tenant has in scope, and which are linked",
    authenticated: true,
    mutates: false,
  },
  {
    method: "PUT",
    path: "/api/repositories",
    summary: "Set which repositories are in scope for this tenant",
    authenticated: true,
    mutates: true,
  },
  {
    method: "DELETE",
    path: "/api/link/github",
    summary: "Revoke the GitHub grant and drop the linked account",
    authenticated: true,
    mutates: true,
  },
  {
    method: "GET",
    path: "/api/runs",
    summary: "This tenant's runs, newest first",
    authenticated: true,
    mutates: false,
  },
  {
    method: "GET",
    path: "/api/runs/[runId]",
    summary: "One run: stages, decisions, security verdict, open gates",
    authenticated: true,
    mutates: false,
  },
  {
    method: "GET",
    path: "/api/runs/[runId]/events",
    summary: "Server-sent events for one run. Resumes from ?since=<cursor>",
    authenticated: true,
    mutates: false,
  },
  {
    method: "GET",
    path: "/api/runs/[runId]/cost",
    summary: "What this run cost, per stage, with the cache finding",
    authenticated: true,
    mutates: false,
  },
  {
    method: "GET",
    path: "/api/runs/[runId]/scoring",
    summary: "Lane C's scoring artifact: one row per finding, native and mapped",
    authenticated: true,
    mutates: false,
  },
  {
    method: "POST",
    path: "/api/approvals",
    summary:
      "Record ONE gate decision. Authenticated, authorised per repository, " +
      "Origin-checked, audited with the session's identity",
    authenticated: true,
    mutates: true,
  },
] as const;

/** Response type per endpoint, for Lane J's fetch wrappers. */
/**
 * The run list.
 *
 * `indexed` IS NOT DECORATION, and it is the difference between two facts an empty
 * `runs` array cannot tell apart:
 *
 *   `indexed: false` — nothing indexes runs in this deployment (`TENANT_DB` is
 *                      unset, so `run_index.record_run` is a no-op by design). A
 *                      screen must SAY so rather than showing "no runs yet".
 *   `indexed: true`  — runs are indexed and this tenant has had none.
 *
 * Lane J: render these differently. An empty list presented as "no runs yet" when
 * nothing is recording them is the "did not run versus passed" conflation this
 * repository exists to prevent, on a screen.
 */
export type RunListResponse = { runs: RunSummary[]; indexed: boolean };
export type RunDetailResponse = RunDetail;
export type RepositoryListResponse = { repositories: RepositoryView[] };

/**
 * The scoring artifact response.
 *
 * `threshold` is echoed at the top level as well as on every row, deliberately.
 * A run with NO findings has no rows, and the threshold that produced that empty
 * table is still a fact worth rendering -- otherwise a clean run and an unscanned
 * one show the same blank table.
 */
export type ScoringResponse = {
  run_id: string;
  threshold: Severity;
  rows: ScoreRow[];
  /** `""` means nobody recorded provenance. Render as unknown, never as a scan. */
  scan_provenance: ScanProvenance;
};
