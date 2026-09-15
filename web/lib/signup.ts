/**
 * Self-service sign-up: create an account, verify the email with a code, resend it.
 *
 * **WHAT THIS EXISTS TO SOLVE WAS A MEASURED DEAD END.** `custom:tenant` is declared
 * `Mutable: False`, and an immutable Cognito attribute cannot be written after
 * creation EVEN WHEN IT NEVER HELD A VALUE -- probed against the live pool on
 * 2026-09-09: `InvalidParameterException: user.custom:tenant: Attribute cannot be
 * updated.` Self-registration is deliberately open, and the browser's app client
 * deliberately cannot write that claim, so a person could sign up, sign in, and be
 * permanently unable to see anything -- `/api/session` answering `tenant_id: null`
 * forever. CLAUDE.md carried it as open item 11.
 *
 * **THE ONLY REMAINING MOMENT TO SET THE CLAIM IS CREATION, SO THE SERVER SIGNS THE
 * USER UP.** `infra/cognito/spec.SIGNUP_CLIENT_SPEC` is a second, CONFIDENTIAL app
 * client whose `WriteAttributes` includes `custom:tenant`. What stops a browser using
 * it is that every call must carry a `SECRET_HASH` derived from a secret this module
 * reads from Secrets Manager with the compute role. A client id is public by nature;
 * the secret is not.
 *
 * So the guard is unchanged in substance: **a self-registering user still cannot
 * choose the claim that authorises them.** The server chooses it, and the value never
 * travels through the browser.
 *
 * **EVERY NEW ACCOUNT GETS ITS OWN TENANT, NOT TENANT ZERO.** Handing a signup the
 * default tenant would show them the original deployment's runs -- exactly what
 * `infra/cognito/spec.pool_spec`'s comment refuses, and what CLAUDE.md calls the
 * answer that "would work in a demo". A fresh tenant means an empty run list with
 * `indexed: true`, which is honest and demonstrates the isolation rather than
 * bypassing it.
 *
 * **THE ERRORS ARE DELIBERATELY VAGUE ABOUT WHETHER AN ACCOUNT EXISTS.** Cognito's
 * `UsernameExistsException` is an account-existence oracle: an attacker can
 * enumerate customers by watching sign-up fail. `describeFailure` collapses it into
 * the same message a successful sign-up produces, and the code path is identical --
 * the same shape as `agentorg/api/auth.py` costing the same scrypt work for an
 * unknown key id as for a wrong secret.
 */

import { createHmac } from "node:crypto";

import {
  CognitoIdentityProviderClient,
  ConfirmSignUpCommand,
  ResendConfirmationCodeCommand,
  SignUpCommand,
} from "@aws-sdk/client-cognito-identity-provider";
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";

const REGION = process.env.AWS_REGION ?? "us-east-1";

/**
 * Where `infra/cognito/provision._store_signup_secret` puts the client id and secret.
 * ONE SPELLING, matching `spec.SIGNUP_SECRET_NAME`; a rename on either side makes
 * every sign-up fail with a message about Secrets Manager rather than about Cognito.
 */
const SECRET_NAME =
  process.env.COGNITO_SIGNUP_SECRET_NAME ?? "theagentorg-shared-cognito-signup-client";

export class SignUpRefused extends Error {
  constructor(
    message: string,
    readonly detail = "",
  ) {
    super(message);
    this.name = "SignUpRefused";
  }
}

type SignupClient = { clientId: string; clientSecret: string };

/**
 * The WIRE keys, which are the WRITER's and not this file's.
 *
 * `infra/cognito/provision._store_signup_secret` writes snake_case, because it is
 * Python. A camelCase read here found neither and answered "sign-up is not
 * configured" -- measured against the deployed app on the first live sign-up
 * attempt. The message was right and the cause was a name, so the two spellings are
 * now stated once, here, and `__tests__/signup-secret.test.ts` reads the Python and
 * asserts they still match. Second cross-language drift of the same night; the first
 * was the key layout.
 */
const WIRE_CLIENT_ID = "client_id";
const WIRE_CLIENT_SECRET = "client_secret";

/**
 * Cached for the life of the container. The secret does not rotate per request, and
 * a Secrets Manager call on every sign-up is a cost and a throttle for no benefit.
 * NOT cached across a rotation -- a redeploy replaces the container, which is the
 * documented way this changes.
 */
let cached: SignupClient | null = null;

async function signupClient(): Promise<SignupClient> {
  if (cached) return cached;

  const sm = new SecretsManagerClient({ region: REGION });
  let raw: string | undefined;
  try {
    raw = (await sm.send(new GetSecretValueCommand({ SecretId: SECRET_NAME }))).SecretString;
  } catch (error) {
    // NAMED, because the two causes need different fixes and both present as
    // "sign-up is broken": the secret does not exist (run
    // `python -m infra.cognito.provision`), or the compute role cannot read it
    // (apply `aws_iam_role_policy.amplify_compute_may_read_signup_secret`).
    throw new SignUpRefused(
      "sign-up is not configured",
      `${SECRET_NAME}: ${(error as Error).name}`,
    );
  }
  if (!raw) throw new SignUpRefused("sign-up is not configured", `${SECRET_NAME} is empty`);

  const parsed = JSON.parse(raw) as Record<string, unknown>;
  const clientId = parsed[WIRE_CLIENT_ID];
  const clientSecret = parsed[WIRE_CLIENT_SECRET];
  if (typeof clientId !== "string" || typeof clientSecret !== "string" || !clientId || !clientSecret) {
    throw new SignUpRefused(
      "sign-up is not configured",
      `${SECRET_NAME} has no ${WIRE_CLIENT_ID}/${WIRE_CLIENT_SECRET}`,
    );
  }
  cached = { clientId, clientSecret };
  return cached;
}

/** Cognito's `SECRET_HASH`: base64(HMAC-SHA256(username + clientId, clientSecret)). */
function secretHash(username: string, { clientId, clientSecret }: SignupClient): string {
  return createHmac("sha256", clientSecret).update(username + clientId).digest("base64");
}

function idp(): CognitoIdentityProviderClient {
  return new CognitoIdentityProviderClient({ region: REGION });
}

/**
 * A tenant id for a brand-new account.
 *
 * DERIVED FROM THE `sub` COGNITO MINTS, not from the email. An email is mutable and
 * is PII; a tenant id ends up in a DynamoDB partition key, in an IAM session tag and
 * in CloudTrail. But `sub` is not known until after `SignUp` returns -- and the claim
 * must be supplied IN that call -- so this is a fresh random id instead, which has
 * the same properties and needs nothing to exist first.
 *
 * It must survive an IAM tag value (`agentorg/db/_dynamo.refuse_unusable_tenant`),
 * so: lowercase hex with a `t-` prefix, no `#`, no spaces.
 */
export function newTenantId(): string {
  return `t-${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`;
}

/** Cognito's own rules, applied before the round trip so the message is ours. */
function refuseBadInput(email: string, password: string): void {
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    throw new SignUpRefused("that does not look like an email address");
  }
  // MIRRORS `infra/cognito/spec.pool_spec`'s PasswordPolicy. Restated rather than
  // fetched: `DescribeUserPool` on every keystroke is a round trip to say something
  // the user can be told immediately, and Cognito enforces the real rule regardless.
  const longEnough = password.length >= 12;
  const classes = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((r) => r.test(password));
  if (!longEnough || classes.length < 4) {
    throw new SignUpRefused(
      "that password does not meet the policy",
      "at least 12 characters, with an upper case letter, a lower case letter, a number and a symbol",
    );
  }
}

/**
 * Collapse a Cognito failure into a message that does not reveal whether the
 * account exists. See the module docstring.
 */
function describeFailure(error: unknown): SignUpRefused {
  const name = (error as { name?: string }).name ?? "";
  if (name === "InvalidPasswordException") {
    return new SignUpRefused("that password does not meet the policy");
  }
  if (name === "CodeMismatchException" || name === "ExpiredCodeException") {
    return new SignUpRefused("that code is not valid, or it has expired");
  }
  if (name === "LimitExceededException" || name === "TooManyRequestsException") {
    return new SignUpRefused("too many attempts; wait a minute and try again");
  }
  return new SignUpRefused("sign-up could not be completed", name);
}

/** Create an unconfirmed account with a tenant, and have Cognito email a code. */
export async function startSignUp(email: string, password: string): Promise<{ tenantId: string }> {
  refuseBadInput(email, password);
  const client = await signupClient();
  const tenantId = newTenantId();

  try {
    await idp().send(
      new SignUpCommand({
        ClientId: client.clientId,
        SecretHash: secretHash(email, client),
        Username: email,
        Password: password,
        UserAttributes: [
          { Name: "email", Value: email },
          // THE ONE MOMENT EITHER OF THESE CAN EVER BE SET. Both are
          // `Mutable: False`, so there is no second chance, and the browser's
          // client is permitted to send neither.
          { Name: "custom:tenant", Value: tenantId },
          // WITHOUT THIS THE ACCOUNT SIGNS IN AND EVERY ROUTE REFUSES IT --
          // measured: `/api/session` returned the right tenant while `/api/runs`
          // answered "sign in to see your runs", because
          // `authorize.authorizeSession` refuses a blank role. The role admits an
          // account to the application; the TENANT is what decides what it can
          // see, so this widens nothing beyond their own empty workspace.
          { Name: "custom:role", Value: "reviewer" },
        ],
      }),
    );
  } catch (error) {
    // **THE EXISTENCE ORACLE IS CLOSED BY RETURNING, NOT BY REWORDING.** A first
    // attempt matched the message and still answered a different STATUS -- 202 for
    // a new address, 400 for one already registered -- which is the same oracle
    // one layer down. Measured against the deployed app before this line existed:
    //
    //   new      -> http 202 {"ok":true,"next":"confirm"}
    //   existing -> http 400 {"error":"check your email for a verification code"}
    //
    // So this path now succeeds exactly as a real sign-up does. The caller is told
    // to check their email, which is true: an account exists and they can ask for
    // a code with `action: "resend"`. Nothing new was created and no code was sent.
    if ((error as { name?: string }).name === "UsernameExistsException") {
      return { tenantId: "" };
    }
    throw describeFailure(error);
  }
  return { tenantId };
}

/** Verify the emailed code. After this the account can sign in through the hosted UI. */
export async function confirmSignUp(email: string, code: string): Promise<void> {
  if (!/^\d{6}$/.test(code.trim())) {
    throw new SignUpRefused("that code is not valid, or it has expired");
  }
  const client = await signupClient();
  try {
    await idp().send(
      new ConfirmSignUpCommand({
        ClientId: client.clientId,
        SecretHash: secretHash(email, client),
        Username: email,
        ConfirmationCode: code.trim(),
      }),
    );
  } catch (error) {
    throw describeFailure(error);
  }
}

/** Send the code again. Answers the same way whether or not the account exists. */
export async function resendCode(email: string): Promise<void> {
  const client = await signupClient();
  try {
    await idp().send(
      new ResendConfirmationCodeCommand({
        ClientId: client.clientId,
        SecretHash: secretHash(email, client),
        Username: email,
      }),
    );
  } catch (error) {
    const name = (error as { name?: string }).name ?? "";
    // A NON-EXISTENT ACCOUNT MUST NOT BE DISTINGUISHABLE from a real one that
    // already has a code in flight, so this one swallows rather than reporting.
    if (name === "UserNotFoundException" || name === "InvalidParameterException") return;
    throw describeFailure(error);
  }
}

/** For tests: drop the cached client so a fake can be installed. */
export function resetSignupClientCache(): void {
  cached = null;
}
