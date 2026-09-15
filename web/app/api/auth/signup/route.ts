/**
 * POST /api/auth/signup — create an account and have Cognito email a code.
 *
 * **POST ONLY.** This creates a record and sends mail, so a GET would be reachable
 * by a back button, a bookmark or a browser prefetch — the same rule
 * `POST /api/approvals` follows, and for the same reason.
 *
 * **THE TENANT IS CHOSEN HERE, SERVER-SIDE, AND THE REQUEST CANNOT INFLUENCE IT.**
 * `custom:tenant` is `Mutable: False`, so it can only ever be set at creation; a
 * body carrying its own tenant would let a self-registration read another
 * customer's runs permanently. `lib/signup.ts` mints one and the body is never
 * consulted for it — the same rule that keeps `by` out of `ApprovalRequest`.
 *
 * **IT DOES NOT SIGN ANYONE IN.** No cookie is set and no token is issued: the
 * account is UNCONFIRMED until the code is verified, and sign-in then happens
 * through the hosted UI like every other session. So a failure here cannot create a
 * half-authenticated state, and this route never becomes a second way to get a
 * session.
 *
 * **THE RESPONSE IS THE SAME WHETHER OR NOT THE ADDRESS IS ALREADY REGISTERED.**
 * Cognito's `UsernameExistsException` is an account-existence oracle; `lib/signup`
 * collapses it into the identical instruction. A caller learns only what they
 * already knew.
 */

import { NextResponse, type NextRequest } from "next/server";

import { SignUpRefused, startSignUp } from "@/lib/signup";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest): Promise<Response> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { error: "the request could not be parsed" },
      { status: 400 },
    );
  }

  const { email, password } = (body ?? {}) as Record<string, unknown>;
  if (typeof email !== "string" || typeof password !== "string") {
    return NextResponse.json(
      { error: "an email address and a password are required" },
      { status: 400 },
    );
  }

  try {
    await startSignUp(email.trim().toLowerCase(), password);
  } catch (error) {
    if (error instanceof SignUpRefused) {
      // 400, NOT 500, and never 409: a 409 would restore the existence oracle
      // that `describeFailure` just closed.
      return NextResponse.json(
        { error: error.message, detail: error.detail },
        { status: 400 },
      );
    }
    return NextResponse.json({ error: "sign-up could not be completed" }, { status: 500 });
  }

  // THE TENANT IS NOT ECHOED. It is not the caller's to know, it is not useful to
  // them, and a value that appears in a response is a value somebody will send back.
  return NextResponse.json({ ok: true, next: "confirm" }, { status: 202 });
}
