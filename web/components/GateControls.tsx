/**
 * GATE CONTROLS. Approve or reject, in the product. THE DANGEROUS ONE.
 *
 * A client component, necessarily: it holds the reason text, the in-flight state
 * and the confirmation step.
 *
 * THREE THINGS THIS COMPONENT REFUSES TO SEND, and each refusal is structural
 * rather than a validation message.
 *
 * 1. NO `by`. It is not a prop, not state, and not in the body. The server takes
 *    it from the session. `approve_server.py` records `by="ui-reviewer"` for every
 *    decision because with no authentication it "genuinely does not know who
 *    clicked"; this surface does know, and a `by` a client could set would let a
 *    caller attribute their approval to somebody else -- on the one field whose
 *    entire purpose is attributing a decision to a person.
 * 2. NO `tenant_id`. No request body in this application carries one.
 * 3. NO `overridden`. It is in the `Decision` type so a screen can RENDER a row
 *    `gates_cli` wrote, and the route refuses it with 422. So this component
 *    displays it and cannot send it: the two buttons are the only paths, and
 *    overriding a security block deliberately still requires shell access.
 *    Widening a network endpoint past a shell is the wrong direction.
 *
 * WHY REJECT ASKS FOR A REASON AND APPROVE DOES NOT
 * =================================================
 * A rejection is the more consequential of the two for the person who has to act
 * on it: an approval moves the run forward and the run itself is the record,
 * while a rejection ends it and leaves somebody asking why. The reason is
 * therefore required on the refusal path and optional on the approval path. That
 * is an asymmetry in the interface, on purpose, and it is the only one.
 */

"use client";

import { useState } from "react";

import { ErrorState } from "@/components/primitives";
import { sendJson } from "@/components/fetching";
import type { Gate } from "@/lib/contract";
import type { ApprovalRequest, ApprovalResponse } from "@/lib/endpoints";

type Pending = "approve" | "reject" | null;

export function GateControls({
  runId,
  gate,
  onRecorded,
}: {
  runId: string;
  gate: Gate;
  /** Called after the server confirms, so the parent re-reads the run. */
  onRecorded: (answer: ApprovalResponse) => void;
}) {
  const [confirming, setConfirming] = useState<Pending>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ error: string; fix: string; detail?: string } | null>(
    null,
  );

  async function submit(decision: "approved" | "rejected") {
    setBusy(true);
    setFailure(null);
    // The body is built here and nowhere else, so what crosses the wire is
    // visible in one place: three fields, and an optional reason.
    const body: ApprovalRequest = {
      run_id: runId,
      gate,
      decision,
      ...(reason.trim() ? { reason: reason.trim() } : {}),
    };
    const result = await sendJson<ApprovalResponse>("POST", "/api/approvals", body);
    setBusy(false);

    if (!result.ok) {
      setFailure({ error: result.error, fix: result.fix, detail: result.detail });
      return;
    }
    // `recorded: false` is a refusal carried on a 200. Treating it as success is
    // exactly the defect this project exists to prevent, so it is handled as a
    // failure with the server's own status quoted back.
    if (!result.value.recorded) {
      setFailure({
        error: "The server did not record that decision.",
        fix: "Reload to see the run's current state before trying again.",
        detail: `run status: ${result.value.status}`,
      });
      return;
    }
    setConfirming(null);
    setReason("");
    onRecorded(result.value);
  }

  const rejectNeedsReason = confirming === "reject" && reason.trim().length === 0;

  return (
    <div
      className="card"
      style={{ borderColor: "var(--accent)", borderLeftWidth: "3px" }}
    >
      <p className="eyebrow" style={{ color: "var(--accent)" }}>
        {gate} · waiting for your decision
      </p>
      <p className="prose" style={{ color: "var(--text)", marginTop: 0 }}>
        Approving lets the run continue to the next stage. Rejecting ends it. Either
        way the decision is recorded against the account you signed in with, and it
        cannot be edited afterwards.
      </p>

      {confirming === null ? (
        <div style={{ display: "flex", gap: "var(--gap-3)", flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn btn-approve"
            onClick={() => setConfirming("approve")}
          >
            Approve {gate}
          </button>
          <button
            type="button"
            className="btn btn-reject"
            onClick={() => setConfirming("reject")}
          >
            Reject {gate}
          </button>
        </div>
      ) : (
        <div>
          <p
            style={{
              fontSize: "var(--step-body)",
              color: confirming === "reject" ? "var(--refused)" : "var(--shipped)",
            }}
          >
            {confirming === "approve"
              ? `Approve ${gate}? The run continues to the next stage.`
              : `Reject ${gate}? The run ends here and the change is not merged.`}
          </p>

          <label
            htmlFor={`reason-${gate}`}
            className="eyebrow"
            style={{ display: "block" }}
          >
            {confirming === "reject" ? "Reason (required)" : "Reason (optional)"}
          </label>
          <textarea
            id={`reason-${gate}`}
            className="field"
            rows={3}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder={
              confirming === "reject"
                ? "What has to change before this can ship?"
                : "Anything worth recording alongside the approval"
            }
            style={{ marginBottom: "var(--gap-3)", resize: "vertical" }}
          />

          <div style={{ display: "flex", gap: "var(--gap-3)", flexWrap: "wrap" }}>
            <button
              type="button"
              className={confirming === "approve" ? "btn btn-approve" : "btn btn-reject"}
              disabled={busy || rejectNeedsReason}
              onClick={() => submit(confirming === "approve" ? "approved" : "rejected")}
            >
              {busy
                ? "Recording…"
                : confirming === "approve"
                  ? "Record approval"
                  : "Record rejection"}
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => {
                setConfirming(null);
                setFailure(null);
              }}
            >
              Cancel
            </button>
          </div>

          {rejectNeedsReason ? (
            <p
              style={{
                marginTop: "var(--gap-3)",
                fontSize: "var(--step-small)",
                color: "var(--text-muted)",
              }}
            >
              A rejection needs a reason — whoever picks this up next has only what
              you write here.
            </p>
          ) : null}
        </div>
      )}

      {failure ? (
        <div style={{ marginTop: "var(--gap-4)" }}>
          <ErrorState error={failure.error} fix={failure.fix} detail={failure.detail} />
        </div>
      ) : null}
    </div>
  );
}

/**
 * Decisions already on the record, including one this UI cannot make.
 *
 * `overridden` renders here as a first-class row: it is what
 * `gates_cli resume --decision overridden` writes, and a union that could not
 * display it would make a real override look like a corrupt record.
 */
export function DecisionLog({
  decisions,
}: {
  decisions: readonly {
    gate: string;
    decision: string;
    by: string;
    at: string;
    reason: string;
  }[];
}) {
  if (decisions.length === 0) {
    return (
      <p className="prose" style={{ fontSize: "var(--step-small)" }}>
        No gate decisions recorded yet.
      </p>
    );
  }

  return (
    <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {decisions.map((d, i) => {
        const tone =
          d.decision === "approved"
            ? "var(--shipped)"
            : d.decision === "rejected"
              ? "var(--refused)"
              : "var(--accent)";
        return (
          <li
            key={`${d.gate}-${d.at}-${i}`}
            style={{
              borderLeft: `2px solid ${tone}`,
              paddingLeft: "var(--gap-3)",
              marginBottom: "var(--gap-4)",
            }}
          >
            <p style={{ margin: 0, fontFamily: "var(--mono)", fontSize: "var(--step-small)" }}>
              <span style={{ color: tone }}>{d.decision}</span>{" "}
              <span style={{ color: "var(--text-muted)" }}>{d.gate}</span>
            </p>
            <p
              style={{
                margin: "var(--gap-1) 0 0",
                fontSize: "var(--step-small)",
                color: "var(--text-muted)",
              }}
            >
              {d.by} · {d.at}
            </p>
            {d.reason ? (
              <p style={{ margin: "var(--gap-2) 0 0", fontSize: "var(--step-small)" }}>
                {d.reason}
              </p>
            ) : null}
            {/* An override did not come from this screen, and saying where it came
                from is the difference between a record and a mystery. */}
            {d.decision === "overridden" ? (
              <p
                style={{
                  margin: "var(--gap-2) 0 0",
                  fontSize: "var(--step-caption)",
                  color: "var(--text-muted)",
                }}
              >
                Recorded from the command line. This screen cannot override a gate.
              </p>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
