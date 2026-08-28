/**
 * The Auth.js route handler. Sign in, sign out, callback, CSRF token.
 *
 * Two exports, and none of the behaviour is ours: Auth.js owns the OAuth dance, the
 * `state` parameter, the PKCE verifier and the CSRF token on its own POSTs. Writing
 * any of that by hand, on a surface that can approve a security gate, would be the
 * worst kind of not-invented-here.
 *
 * WHAT AUTH.JS PROTECTS AND WHAT IT DOES NOT
 * =========================================
 * It carries a double-submit CSRF token on ITS OWN sign-in and sign-out POSTs. It
 * does **not** protect `POST /api/approvals` — that is a different endpoint, and its
 * cross-site defence is the `Origin` check in `web/lib/authz.ts`. Naming this is the
 * point: "Auth.js has CSRF protection" is true, and does not cover the one route
 * that opens a gate.
 *
 * BOTH METHODS ARE NEEDED. The redirect out to GitHub and the callback back are
 * GETs; sign-in and sign-out are POSTs, so they cannot be triggered by a link, an
 * `<img>` or a prefetch — the same reason `approve_server` makes every mutation a
 * POST.
 */

import { handlers } from "@/lib/auth";

export const { GET, POST } = handlers;
