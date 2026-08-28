/**
 * FETCHING, and what a failure looks like on the way out.
 *
 * `web/lib/**` is LANE I's. This file lives in `components/` deliberately: it is
 * the client's half of the contract, not the server's, and putting it in `lib/`
 * would be editing another lane's directory while it is still working.
 *
 * ONE FUNCTION, ONE FAILURE SHAPE. Every route answers `ApiError { error,
 * detail? }`, so a caller needs exactly two outcomes: the value, or something it
 * can hand to `<ErrorState>`. That is why this returns a discriminated union
 * rather than throwing -- a thrown error has to be caught identically at eight
 * call sites, and the one that forgets renders a blank screen with a stack trace
 * in the console.
 *
 * THE THREE FAILURES A NAIVE WRAPPER CANNOT TELL APART, all handled here:
 *
 *   the request never arrived      -> `fetch` rejects (offline, DNS, refused)
 *   it arrived and was refused     -> !res.ok, body is an ApiError
 *   it arrived, was refused, and the body is not JSON
 *                                  -> a proxy or the framework answered, not us
 *
 * The third is the one worth the code. A 502 from something in front of the app
 * returns HTML, so `res.json()` throws INSIDE the error path and the caller sees
 * "Unexpected token '<'" -- a parse error standing in for a service that is down.
 * Each gets its own sentence and its own recourse.
 */

import type { ApiError } from "@/lib/endpoints";

/** What a screen renders: a value, or an error with a fix. */
export type Result<T> =
  | { ok: true; value: T }
  | { ok: false; error: string; fix: string; detail?: string };

/** The recourse for a status code. A 401 and a 500 need different actions. */
function fixFor(status: number): string {
  if (status === 401) return "Sign in and try again.";
  if (status === 403) {
    return (
      "This account cannot see that. If it should, ask whoever administers the " +
      "tenant to add the repository to its scope."
    );
  }
  if (status === 404) {
    return "Check the id in the address bar. Nothing here matches it.";
  }
  if (status === 409) {
    return "Reload to see the current state — this changed since the page loaded.";
  }
  if (status === 422) return "Correct the highlighted value and resend.";
  if (status === 429) return "Wait a moment and retry.";
  if (status >= 500) {
    return "Retry once. If it happens again this needs an operator, not a refresh.";
  }
  return "Reload the page.";
}

/**
 * GET a JSON endpoint.
 *
 * `cache: "no-store"` on every call, and it is not a performance oversight: this
 * app's answers are a run's live status, a security verdict and a cost. A cached
 * verdict is the worst possible thing to render -- a person would approve a gate
 * against a state that has already moved.
 */
export async function getJson<T>(path: string): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetch(path, {
      cache: "no-store",
      headers: { accept: "application/json" },
    });
  } catch {
    // The request never left, or nothing answered. NOT a server error: saying
    // "the server failed" when the network is down sends the reader to the
    // wrong place.
    return {
      ok: false,
      error: "The request did not reach the server.",
      fix: "Check the connection and retry.",
    };
  }

  if (!res.ok) {
    const parsed = await readError(res);
    return { ok: false, error: parsed.error, fix: fixFor(res.status), detail: parsed.detail };
  }

  try {
    return { ok: true, value: (await res.json()) as T };
  } catch {
    // A 200 whose body is not JSON. Rare and worth its own sentence: it means
    // something answered successfully with the wrong thing.
    return {
      ok: false,
      error: "The server answered successfully but the response was not readable.",
      fix: "Retry once. If it repeats this needs an operator.",
      detail: `${res.status} ${res.statusText} from ${path}`,
    };
  }
}

/**
 * Send a body. POST, PUT or DELETE.
 *
 * `same-origin` credentials and a JSON content type, which is also what makes
 * the server's `Origin` check meaningful. No `mode: "cors"` -- every one of
 * these routes is this app's own.
 */
export async function sendJson<T>(
  method: "POST" | "PUT" | "DELETE",
  path: string,
  body?: unknown,
): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      cache: "no-store",
      credentials: "same-origin",
      headers: {
        accept: "application/json",
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch {
    return {
      ok: false,
      // A MUTATION THAT MAY OR MAY NOT HAVE LANDED IS ITS OWN FACT. "Retry"
      // alone is wrong advice for a gate decision: the first attempt may have
      // been recorded. So this says what is unknown.
      error: "The request did not reach the server, so nothing was recorded.",
      fix: "Check the connection and retry. Reload first to confirm the current state.",
    };
  }

  if (!res.ok) {
    const parsed = await readError(res);
    return { ok: false, error: parsed.error, fix: fixFor(res.status), detail: parsed.detail };
  }

  try {
    return { ok: true, value: (await res.json()) as T };
  } catch {
    return {
      ok: false,
      error: "The change may have been recorded, but the response was not readable.",
      fix: "Reload to see whether it took effect. Do not resend blindly.",
      detail: `${res.status} ${res.statusText} from ${path}`,
    };
  }
}

/**
 * Read an error body, tolerating one that is not ours.
 *
 * The status line is the detail rather than the body text, because a proxy's
 * HTML error page is hundreds of lines and pasting it into the UI would bury the
 * one sentence a reader needs. And the body is never echoed as the headline:
 * `endpoints.ts` notes `approve_server._one` "already refuses to echo" caller
 * text, and an error page rendering upstream text is the same hazard.
 */
async function readError(res: Response): Promise<{ error: string; detail: string }> {
  const detail = `HTTP ${res.status} ${res.statusText}`;
  try {
    const body = (await res.json()) as Partial<ApiError>;
    if (typeof body.error === "string" && body.error.length > 0) {
      return {
        error: body.error,
        detail: body.detail ? `${detail} — ${body.detail}` : detail,
      };
    }
  } catch {
    // Not JSON. Fall through: something other than this app answered.
  }
  return {
    error:
      res.status >= 500
        ? "The server could not complete the request."
        : "The request was refused.",
    detail,
  };
}
