/**
 * The two auth doors, through the forms a customer uses.
 *
 * Every spec that needs data of its own creates its own organisation. That is what makes
 * the suite safe to run repeatedly against a long-lived stack: a run can only ever see
 * the traces it sent, and there is nothing to clean up afterwards.
 *
 * Both doors are rate limited, and the whole suite shares one bucket behind the web
 * container's proxy — so every submit here claims from the ledger in `auth-quota.ts`
 * first. Nothing else in the suite may post to /signup or /auth/login.
 */

import { randomUUID } from "node:crypto";

import { type Page, type Response, test } from "@playwright/test";

import { deferAuthRequests, takeAuthAllowance } from "./auth-quota";
import { workerAddress } from "./worker-client";

export interface NewAccount {
  company: string;
  email: string;
  password: string;
}

const SIGNUP_PATH = "/api/v1/signup";
const LOGIN_PATH = "/api/v1/auth/login";

const TOO_MANY_REQUESTS = 429;

/** Pacing should make a retry unnecessary. This is for when it is not. */
const MAX_ATTEMPTS = 3;

/** Unique per call, so two runs — or two workers — never collide on the email. */
export function newAccount(): NewAccount {
  const suffix = randomUUID().slice(0, 8);
  return {
    company: `End To End ${suffix}`,
    // .test is reserved by RFC 2606 and can never be a real inbox.
    email: `e2e-${suffix}@nexovue.test`,
    password: `e2e-${suffix}-password`,
  };
}

/** Fills the real form and submits it. The product decides where the browser lands. */
export async function signUp(page: Page, account: NewAccount): Promise<void> {
  await asThisWorkersClient(page);
  await page.goto("/signup");
  await page.getByLabel("Company").fill(account.company);
  await page.getByLabel("Email").fill(account.email);
  await page.getByLabel("Password").fill(account.password);
  // "Your name" is left blank on purpose: the schema allows a nameless owner.
  await submitAuthForm(page, "Create account", SIGNUP_PATH);
}

/** The returning user's door. A rejected password is the product answering, not a fault. */
export async function signIn(page: Page, email: string, password: string): Promise<void> {
  await asThisWorkersClient(page);
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await submitAuthForm(page, "Sign in", LOGIN_PATH);
}

/**
 * Everything this page sends from now on comes from this worker's own address.
 *
 * Set here rather than in a fixture so no spec has to know about it: the auth
 * doors are the only rate limit the suite can collide with, and both of them go
 * through this file. See `worker-client.ts` for why four workers are four
 * callers rather than one.
 */
async function asThisWorkersClient(page: Page): Promise<void> {
  await page.setExtraHTTPHeaders({ "x-forwarded-for": workerAddress() });
}

/**
 * Submits, and reads the outcome off the response rather than off the screen.
 *
 * "You are going too fast" and "that password is wrong" are both a line of text in the
 * form, and only one of them is the suite's own fault. The status code tells them apart
 * exactly, so a retry can never paper over a real rejection.
 */
async function submitAuthForm(page: Page, button: string, path: string): Promise<void> {
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    await waitForAuthAllowance();

    const [response] = await Promise.all([
      page.waitForResponse((candidate) => candidate.url().includes(path)),
      page.getByRole("button", { name: button }).click(),
    ]);

    if (response.status() !== TOO_MANY_REQUESTS) return;
    await deferAuthRequests(retryAfterSeconds(response));
  }

  throw new Error(
    `${path} was rate limited ${MAX_ATTEMPTS} times running: the suite is outspending its ` +
      `share of the auth allowance, or something else is signing in against this stack.`,
  );
}

/**
 * Queueing for the shared allowance, on the suite's clock rather than the test's.
 *
 * A test held behind eight other signups has not been slow; it has not started. Charging
 * that wait to its own deadline is how a correctly paced suite still fails on timeouts —
 * measured at 50s of a 60s budget spent queueing. So the deadline moves by exactly what
 * the gate held, and the test keeps the whole budget it was written with.
 */
async function waitForAuthAllowance(): Promise<void> {
  const startedAt = Date.now();
  await takeAuthAllowance();
  const queuedMs = Date.now() - startedAt;
  if (queuedMs > 0) test.setTimeout(test.info().timeout + queuedMs);
}

/** What the API said to wait, when it said anything intelligible. */
function retryAfterSeconds(response: Response): number | undefined {
  const seconds = Number(response.headers()["retry-after"]);
  return Number.isFinite(seconds) ? seconds : undefined;
}
