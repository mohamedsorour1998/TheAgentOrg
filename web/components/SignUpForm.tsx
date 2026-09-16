/**
 * CREATE AN ACCOUNT — two steps, because Cognito's flow has two and pretending
 * otherwise would strand people.
 *
 * Step 1 posts an email and a password to `/api/auth/signup`; Cognito creates an
 * UNCONFIRMED account and emails a six-digit code. Step 2 posts that code to
 * `/api/auth/confirm`. Only then can the account sign in, through the hosted UI
 * like every other session.
 *
 * **THIS FORM CANNOT CHOOSE A TENANT, AND THAT IS THE WHOLE DESIGN.**
 * `custom:tenant` decides whose runs an account may read. It is `Mutable: False`,
 * so it can only be set at creation, and the browser's Cognito client is not
 * permitted to write it at all — the server picks the value in
 * `lib/signup.startSignUp` and it never appears in a request or a response. There
 * is deliberately no field for it here, and adding one would do nothing, which is
 * the correct outcome for a value a customer must not select.
 *
 * **NO PASSWORD IS EVER SENT ANYWHERE BUT `/api/auth/signup`.** The sign-IN form
 * lives on Cognito's hosted UI precisely so this application never receives a
 * password — `infra/cognito/provision.py` records that as the reason the hosted
 * domain exists. Sign-UP is the one exception, and it is bounded: the route creates
 * an account and issues no session, so a password reaching it cannot become one.
 *
 * **THE ANSWER IS THE SAME WHETHER OR NOT THE ADDRESS IS ALREADY REGISTERED**, so
 * this screen must not imply otherwise. "Check your email" is shown on both paths;
 * a message like "that address is taken" would turn this form into a way to
 * enumerate customers.
 */

"use client";

import { useCallback, useState } from "react";

type Step = "details" | "code" | "done";

/** The shape both routes answer with. `detail` is the fix, when there is one. */
type Answer = { error?: string; detail?: string; next?: string };

async function post(path: string, body: unknown): Promise<Answer> {
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    // A non-JSON body is a real possibility — a proxy error page, a 502 — and
    // `json()` on one throws a parse error that reads as nonsense to a person.
    const parsed = (await response.json().catch(() => ({}))) as Answer;
    if (!response.ok) {
      return { error: parsed.error ?? "that could not be completed", detail: parsed.detail };
    }
    return parsed;
  } catch {
    return { error: "the network request did not complete" };
  }
}

export function SignUpForm() {
  const [step, setStep] = useState<Step>("details");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Answer | null>(null);
  const [notice, setNotice] = useState("");

  const submitDetails = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setBusy(true);
      setProblem(null);
      const answer = await post("/api/auth/signup", { email, password });
      setBusy(false);
      if (answer.error) {
        setProblem(answer);
        return;
      }
      // The password is dropped from state the moment it is no longer needed. It
      // cannot be read back from here, but leaving it in a component that stays
      // mounted for the rest of the visit is gratuitous.
      setPassword("");
      setNotice(`A six-digit code is on its way to ${email}.`);
      setStep("code");
    },
    [email, password],
  );

  const submitCode = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setBusy(true);
      setProblem(null);
      const answer = await post("/api/auth/confirm", { email, code });
      setBusy(false);
      if (answer.error) {
        setProblem(answer);
        return;
      }
      setStep("done");
    },
    [email, code],
  );

  const resend = useCallback(async () => {
    setBusy(true);
    setProblem(null);
    await post("/api/auth/confirm", { email, action: "resend" });
    setBusy(false);
    // NO SUCCESS/FAILURE DISTINCTION, deliberately: the route answers the same way
    // for an address that does not exist, and a message here that could tell them
    // apart would reopen the oracle the route closed.
    setNotice(`If that address has an account awaiting confirmation, another code is on its way.`);
  }, [email]);

  if (step === "done") {
    return (
      <div className="card" style={{ maxWidth: "var(--measure)" }}>
        <p className="eyebrow">Account confirmed</p>
        <p className="title" style={{ marginBottom: "var(--gap-4)" }}>
          Your email is verified
        </p>
        <p className="prose" style={{ margin: `0 0 var(--gap-4)`, fontSize: "var(--step-small)" }}>
          Sign in below. Your account has its own organisation, so it starts with no
          runs — that is an empty workspace, not a permission you are missing.
        </p>
        <form action="/api/auth/signin" method="get">
          <button type="submit" className="btn">
            Sign in
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="card" style={{ maxWidth: "var(--measure)" }}>
      <p className="eyebrow">New here</p>
      <p className="title" style={{ marginBottom: "var(--gap-4)" }}>
        Create an account
      </p>

      {problem ? (
        <p
          className="prose"
          style={{
            margin: `0 0 var(--gap-4)`,
            fontSize: "var(--step-small)",
            color: "var(--refused)",
          }}
          role="alert"
        >
          {problem.error}
          {problem.detail ? <span style={{ opacity: 0.8 }}> — {problem.detail}</span> : null}
        </p>
      ) : null}

      {notice && !problem ? (
        <p
          className="prose"
          style={{ margin: `0 0 var(--gap-4)`, fontSize: "var(--step-small)" }}
          role="status"
        >
          {notice}
        </p>
      ) : null}

      {step === "details" ? (
        <form onSubmit={submitDetails} style={{ display: "grid", gap: "var(--gap-3)" }}>
          <label style={{ display: "grid", gap: "var(--gap-1)" }}>
            <span className="eyebrow">Email</span>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={{ padding: "var(--gap-2)", font: "inherit" }}
            />
          </label>
          <label style={{ display: "grid", gap: "var(--gap-1)" }}>
            <span className="eyebrow">Password</span>
            <input
              type="password"
              required
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={{ padding: "var(--gap-2)", font: "inherit" }}
            />
          </label>
          {/* STATED UP FRONT rather than discovered by being refused. The policy
              is the pool's, and a person who learns it only from an error has
              already chosen a password twice. */}
          <p className="prose" style={{ margin: 0, fontSize: "var(--step-small)", opacity: 0.8 }}>
            At least 12 characters, with an upper case letter, a lower case letter, a
            number and a symbol.
          </p>
          <div>
            <button type="submit" className="btn" disabled={busy}>
              {busy ? "Creating your account…" : "Create account"}
            </button>
          </div>
        </form>
      ) : (
        <form onSubmit={submitCode} style={{ display: "grid", gap: "var(--gap-3)" }}>
          <label style={{ display: "grid", gap: "var(--gap-1)" }}>
            <span className="eyebrow">Verification code</span>
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="\d{6}"
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
              style={{ padding: "var(--gap-2)", font: "inherit", letterSpacing: "0.3em" }}
            />
          </label>
          <div style={{ display: "flex", gap: "var(--gap-3)", alignItems: "center" }}>
            <button type="submit" className="btn" disabled={busy}>
              {busy ? "Checking…" : "Verify"}
            </button>
            <button
              type="button"
              onClick={resend}
              disabled={busy}
              style={{ background: "none", border: 0, font: "inherit", cursor: "pointer", textDecoration: "underline" }}
            >
              Send another code
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
