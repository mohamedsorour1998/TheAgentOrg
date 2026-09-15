/**
 * Credentials that can only read one tenant. The TypeScript twin of
 * `agentorg/db/tenant_credentials.py`, and it exists because the Python one
 * cannot run where this code runs.
 *
 * **WHY A NODE CLIENT IS NOW CORRECT, WHEN CLAUDE.md SAYS IT IS THE WORST OPTION.**
 * That ruling was measured and right, and it was about SQL: the SQLite triggers
 * compare against `current_tenant()`, an application-defined function only
 * `db.engine.connect()` registers, so a Node connection failed every scoped WRITE
 * loudly while every scoped READ succeeded UNSCOPED. That asymmetry is how a leak
 * ships green.
 *
 * **The DynamoDB migration dissolves that argument entirely.** Isolation is no
 * longer an application-defined SQL function; it is
 * `dynamodb:LeadingKeys` compared against an IAM session tag. A Node client that
 * assumes the same role with the same tag gets the *same* enforcement from AWS --
 * not a reimplementation of it. There is nothing here for a client to get wrong,
 * because nothing here is doing the scoping.
 *
 * **WHY IT HAD TO MOVE.** Measured 2026-09-15 against the deployed app: every data
 * route answered
 *
 *     PipelineError: the pipeline reader could not be started
 *
 * `web/lib/pipeline.ts` spawned `.venv-main/bin/python`, and an Amplify SSR Lambda
 * has no Python, no virtualenv and no repository checkout. So the Python readers
 * were correct code that could never start -- this repository's signature pattern,
 * at the largest scale it has appeared. Sign-in worked, the session carried
 * `tenant_id: tenant-zero`, and every screen behind it was an error.
 *
 * THREE REFUSALS, EACH WITH A FAIL-OPEN VERSION THAT READS AS CORRECT CODE, and
 * they are the same three the Python module documents:
 *
 *   no tenant      -> throw. An empty id builds `TENANT#`, which is the partition
 *                     an UNTAGGED session is authorised for, so an unscoped caller
 *                     and an unnamed tenant would share rows.
 *   no role ARN    -> throw. NEVER fall back to the ambient credential. The
 *                     fallback is one line, passes every test, and hands back the
 *                     Amplify compute role -- which can reach the table across
 *                     every tenant. A leak that appears only when a variable is
 *                     unset is the worst shape available.
 *   STS refuses    -> throw, naming the tag. The tempting fix for an AccessDenied
 *                     here is to drop `Tags`, which SUCCEEDS and mints a session
 *                     whose `aws:PrincipalTag/tenant` is empty -- so LeadingKeys
 *                     compares against `TENANT#`, matches nothing, and the symptom
 *                     is "the database is broken".
 */

import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient } from "@aws-sdk/lib-dynamodb";
import { AssumeRoleCommand, STSClient } from "@aws-sdk/client-sts";

/**
 * The tag key. ONE SPELLING, and it must equal the one the policy compares
 * against -- `infra/Terraform/modules/tenancy/iam.tf` authorises
 * `TENANT#${aws:PrincipalTag/tenant}`.
 *
 * A mismatch raises NOWHERE: STS accepts a tag called anything, the session
 * carries it, and `aws:PrincipalTag/tenant` is then empty -- so LeadingKeys matches
 * nothing and every read is AccessDenied for a reason no error message mentions.
 */
export const TAG_KEY = "tenant";

/** Seconds of validity to discard before expiry. See the Python module. */
const EXPIRY_MARGIN_MS = 120_000;

/** The AWS floor for a chained session, and what the role's max is set to. */
const SESSION_SECONDS = 3600;

const REGION = process.env.AWS_REGION ?? "us-east-1";

type Cached = { client: DynamoDBDocumentClient; expiresAt: number };

/**
 * Keyed by tenant. **THE KEY IS NOT AN OPTIMISATION DETAIL.** A cache keyed on
 * nothing -- one module-level client -- is the leak this whole subsystem exists to
 * prevent, arriving through the optimisation: the first request mints tenant A's
 * client, every later request reuses it, and tenant B reads A's rows with the
 * correct policy applied to the wrong tag. Nothing raises, and the rows come back.
 */
const CACHE = new Map<string, Cached>();

/** A tenant id must survive an IAM tag: letters, digits, space and `_ . : / = + - @`. */
const TAG_SAFE = /^[A-Za-z0-9_.:/=+\-@ ]+$/;

export class TenantCredentialError extends Error {}

function refuseUnusableTenant(tenantId: string): void {
  if (!tenantId) {
    throw new TenantCredentialError(
      "tenant_id is empty. An empty id builds the partition `TENANT#`, which is " +
        "the SAME partition an untagged IAM session is authorised for -- so an " +
        "unscoped caller and an unnamed tenant would share rows.",
    );
  }
  if (!TAG_SAFE.test(tenantId)) {
    throw new TenantCredentialError(
      `tenant_id ${JSON.stringify(tenantId)} contains characters IAM refuses in a ` +
        "session tag value. The tenant travels to DynamoDB AS a tag, so this id " +
        "could never be scoped -- AssumeRole would fail at STS with a message " +
        "about the tag rather than about the tenant.",
    );
  }
}

/**
 * A DynamoDB document client that can read `tenantId`'s partition and no other.
 *
 * Returned as a DOCUMENT client so callers write plain JavaScript values; the
 * marshalling is the SDK's, not ours, which keeps one fewer thing able to disagree
 * with the Python writer's item shapes.
 */
export async function scopedClient(tenantId: string): Promise<DynamoDBDocumentClient> {
  refuseUnusableTenant(tenantId);

  const roleArn = (process.env.TENANT_SCOPED_ROLE_ARN ?? "").trim();
  if (!roleArn) {
    throw new TenantCredentialError(
      "TENANT_SCOPED_ROLE_ARN is empty, so there is no role to assume and no " +
        "tenant-scoped credential to mint. This REFUSES rather than falling back " +
        "to the ambient credential on purpose: the fallback would use this " +
        "runtime's own role, which can read every tenant's rows, and would do so " +
        "only on the machines where the variable is unset.",
    );
  }

  const cached = CACHE.get(tenantId);
  if (cached && Date.now() < cached.expiresAt - EXPIRY_MARGIN_MS) {
    return cached.client;
  }

  const sts = new STSClient({ region: REGION });
  let answer;
  try {
    answer = await sts.send(
      new AssumeRoleCommand({
        RoleArn: roleArn,
        RoleSessionName: `tenant-${tenantId}`.slice(0, 64),
        DurationSeconds: SESSION_SECONDS,
        // THE TAG IS THE AUTHORISATION. Without it the call still succeeds and
        // returns credentials that can read nothing. Not optional.
        Tags: [{ Key: TAG_KEY, Value: tenantId }],
      }),
    );
  } catch (error) {
    throw new TenantCredentialError(
      `could not assume ${roleArn} as tenant ${JSON.stringify(tenantId)} ` +
        `(${(error as Error).name}). If this is AccessDenied, the caller most ` +
        "likely holds `sts:AssumeRole` and NOT `sts:TagSession` -- the tenancy " +
        "module's trust policy requires both. DO NOT FIX IT BY DROPPING THE TAG: " +
        "that call succeeds, and the session it returns has an empty " +
        `aws:PrincipalTag/${TAG_KEY}, so every read is refused for a reason ` +
        "nothing reports.",
    );
  }

  const c = answer.Credentials;
  if (!c?.AccessKeyId || !c.SecretAccessKey || !c.SessionToken) {
    throw new TenantCredentialError(
      "STS returned no credentials for a call it did not refuse; refusing rather " +
        "than continuing with an unscoped default.",
    );
  }

  const client = DynamoDBDocumentClient.from(
    new DynamoDBClient({
      region: REGION,
      credentials: {
        accessKeyId: c.AccessKeyId,
        secretAccessKey: c.SecretAccessKey,
        sessionToken: c.SessionToken,
      },
    }),
    // `removeUndefinedValues` so an absent optional field is omitted rather than
    // rejected; the Python writer omits the same fields.
    { marshallOptions: { removeUndefinedValues: true } },
  );

  CACHE.set(tenantId, {
    client,
    expiresAt: c.Expiration ? c.Expiration.getTime() : Date.now() + SESSION_SECONDS * 1000,
  });
  return client;
}

/** Drop every cached client. For tests and for a long-lived process between jobs. */
export function resetCredentialCache(): void {
  CACHE.clear();
}
