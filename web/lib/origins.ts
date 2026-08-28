/**
 * WHICH ORIGINS MAY MUTATE. Pure, so it is testable without a database.
 *
 * Split out of `web/lib/auth.ts` deliberately. That module builds a `pg.Pool` and
 * refuses to import without `AUTH_SECRET` and `DATABASE_URL` — which is the right
 * fail-closed behaviour for a deployment and makes it unimportable from a test. A
 * function whose whole job is deciding whether to refuse a mutation must be
 * reachable by the suite, so it lives here with no imports at all.
 *
 * That separation is not cosmetic: `authz.originIsAcceptable` refuses every present
 * `Origin` against an empty list, so if this function silently returned `[]` in a
 * deployment, every approval would be refused and the button would look broken. A
 * test that cannot import it cannot catch that.
 */

/**
 * The origins a mutating request may come from.
 *
 * DERIVED FROM `AUTH_URL`, not a separate knob, because two lists of origins is two
 * declarations of one fact — and when they drift the symptom is a legitimate click
 * being refused, which reads as a broken button rather than as a misconfiguration.
 *
 * An unset or malformed `AUTH_URL` yields an EMPTY list, and `originIsAcceptable`
 * treats that as "refuse every present Origin". Fail-closed, deliberately: the
 * permissive reading — nothing configured, so allow all — is the same shape as an
 * empty key store granting access, which Lane K measured and refused.
 *
 * `URL().origin` NORMALISES, and that is why it is used rather than string
 * handling: it drops a trailing slash, a path, a query and the default port, so
 * `https://app.example/` and `https://app.example:443/dashboard` both yield
 * `https://app.example` — which is the exact string a browser puts in the `Origin`
 * header. Comparing raw configuration text would refuse a legitimate click because
 * somebody left a slash on the end.
 */
export function originsFrom(configured: string | undefined): readonly string[] {
  if (!configured || !configured.trim()) {
    return [];
  }
  try {
    return [new URL(configured).origin];
  } catch {
    // A malformed value yields no origins rather than throwing, so the failure is
    // a refused mutation carrying a message rather than a 500 on every request.
    return [];
  }
}

/** The configured origins, from the environment. */
export function allowedOrigins(): readonly string[] {
  return originsFrom(process.env.AUTH_URL);
}
