/**
 * POST /api/auth/confirm — verify the emailed code, or ask for another one.
 *
 * Two actions on one route because they are one step from a person's point of view:
 * "I have the code" and "I did not get the code". `action: "resend"` sends another;
 * anything else verifies.
 *
 * **CONFIRMING DOES NOT SIGN ANYONE IN**, deliberately. It flips the account to
 * CONFIRMED and nothing else; the browser then goes through the hosted UI like every
 * other session. Issuing a cookie here would make this a second, weaker way to
 * obtain a session — one that never saw a password check — and it is exactly the
 * kind of shortcut that is invisible until somebody notices the route.
 *
 * **NEITHER ACTION REVEALS WHETHER AN ACCOUNT EXISTS.** `resendCode` swallows
 * `UserNotFoundException` and answers the same way regardless, so a caller cannot
 * enumerate customers by watching which addresses accept a resend.
 *
 * A wrong code and an expired code share one message on purpose: telling them apart
 * tells an attacker that the address is real and that a code was recently issued.
 */

import { NextResponse, type NextRequest } from "next/server";

import { SignUpRefused, confirmSignUp, resendCode } from "@/lib/signup";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest): Promise<Response> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "the request could not be parsed" }, { status: 400 });
  }

  const { email, code, action } = (body ?? {}) as Record<string, unknown>;
  if (typeof email !== "string" || !email.trim()) {
    return NextResponse.json({ error: "an email address is required" }, { status: 400 });
  }
  const address = email.trim().toLowerCase();

  try {
    if (action === "resend") {
      await resendCode(address);
      return NextResponse.json({ ok: true, next: "confirm" }, { status: 202 });
    }
    if (typeof code !== "string") {
      return NextResponse.json({ error: "a verification code is required" }, { status: 400 });
    }
    await confirmSignUp(address, code);
  } catch (error) {
    if (error instanceof SignUpRefused) {
      return NextResponse.json(
        { error: error.message, detail: error.detail },
        { status: 400 },
      );
    }
    return NextResponse.json({ error: "the code could not be checked" }, { status: 500 });
  }

  // `next: "signin"` so the screen knows where to send them, rather than the route
  // redirecting: this is an API answer, and a 3xx here would be followed by fetch()
  // and land the hosted UI's HTML in a JSON parser.
  return NextResponse.json({ ok: true, next: "signin" }, { status: 200 });
}
