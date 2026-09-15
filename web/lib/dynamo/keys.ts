/**
 * The single tenancy table's key layout, in TypeScript. PURE FUNCTIONS, NO AWS CALL.
 *
 * A SECOND DECLARATION OF `agentorg/db/_dynamo.py`, AND IT IS DELIBERATE RATHER THAN
 * SLOPPY. The two run in different languages in different processes and neither can
 * import the other, so there is no shared source to take this from. This repository's
 * standing rule is that a second declaration must be *detectable*, not avoided at all
 * costs -- the same exception `tests/test_scoring_determinism.py` makes for
 * `SEVERITY_ORDER`, and for the same reason: two copies keep agreeing while one moves.
 *
 * `web/__tests__/dynamo-keys.test.ts` reads BOTH files and asserts the prefixes,
 * separator and sort-key tokens match, so a rename on either side fails a gate rather
 * than producing a reader that queries a partition nothing writes.
 *
 * THE PARTITION KEY IS THE TENANT, and that is not a convenience:
 * `dynamodb:LeadingKeys` compares against the partition key of the request, so this is
 * the only shape IAM can defend.
 */

/**
 * The separator, and a character that CANNOT appear in a tenant id -- `#` is not in
 * IAM's tag-value alphabet, which is what `refuseUnusableTenant` enforces one module
 * over.
 */
export const SEP = "#";

export const TENANT_PREFIX = "TENANT";
export const USER_PREFIX = "USER";

/** Sort-key tokens. `ORG`, `BUDGET` and `PROFILE` are bare singletons; the rest prefix an id. */
export const SK_ORG = "ORG";
export const SK_BUDGET = "BUDGET";
export const SK_PROFILE = "PROFILE";
export const SK_MEMBER = "MEMBER";
export const SK_REPO = "REPO";
export const SK_RUN = "RUN";
export const SK_SECRET = "SECRET";
export const SK_JOB = "JOB";

/**
 * What `RunState.tenant_id` carries for every run written before tenancy existed.
 *
 * TRANSLATED, NEVER REWRITTEN, and the translation happens in exactly one place --
 * `tenantForRunState` -- mirroring `agentorg/tenancy/tenant_zero.py`. A blank arriving
 * from a SESSION is a refusal, not a default: reading `""` as tenant zero at the
 * boundary would let a caller with no session read the original single-tenant
 * deployment's runs.
 */
export const TENANT_ZERO_ID = "tenant-zero";
export const SINGLE_TENANT_MARKER = "";

/** `TENANT#<tenant_id>` -- the partition every scoped row lives in. */
export function tenantPk(tenantId: string): string {
  if (!tenantId) {
    throw new Error(
      "tenant_id is empty. An empty id builds `TENANT#`, the same partition an " +
        "untagged IAM session is authorised for.",
    );
  }
  return `${TENANT_PREFIX}${SEP}${tenantId}`;
}

/** A sort key: a bare token, or `TOKEN#<identifier>`. */
export function sk(token: string, identifier = ""): string {
  if (!token) throw new Error("a sort key needs a token");
  if (!identifier) {
    if (![SK_ORG, SK_BUDGET, SK_PROFILE].includes(token)) {
      throw new Error(
        `${token} needs an identifier. Only ORG, BUDGET and PROFILE are singletons ` +
          "within their partition; a bare key for anything else makes every row of " +
          "that type overwrite the last one.",
      );
    }
    return token;
  }
  return `${token}${SEP}${identifier}`;
}

/**
 * The database tenant for a `RunState.tenant_id`. THE ONE TRANSLATION POINT.
 *
 * A real tenant id is answered unchanged, so a multi-tenant run is not silently
 * reassigned -- the inverse defect, and it would look identical in the data.
 */
export function tenantForRunState(tenantId: string): string {
  return tenantId === SINGLE_TENANT_MARKER ? TENANT_ZERO_ID : tenantId;
}

/**
 * Whether `runId` may be used to build a key. A POSITIVE test for "one safe
 * component", not a blacklist -- the twin of `agentorg/log.is_safe_run_id`.
 *
 * It deliberately does not reject markup: `<img src=x onerror=alert(1)>` is a safe
 * identifier and a dangerous thing to interpolate into HTML, and those are two
 * problems with two different fixes. React escapes the second.
 */
export function isSafeRunId(runId: string): boolean {
  if (!runId || runId.length > 200) return false;
  if (runId === "." || runId === "..") return false;
  return /^[A-Za-z0-9._-]+$/.test(runId);
}
