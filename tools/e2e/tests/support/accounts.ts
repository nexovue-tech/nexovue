/**
 * Signing up, through the form a customer uses.
 *
 * Every spec that needs data of its own creates its own organisation. That is what makes
 * the suite safe to run repeatedly against a long-lived stack: a run can only ever see
 * the traces it sent, and there is nothing to clean up afterwards.
 */

import { randomUUID } from "node:crypto";

import type { Page } from "@playwright/test";

export interface NewAccount {
  company: string;
  email: string;
  password: string;
}

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
  await page.goto("/signup");
  await page.getByLabel("Company").fill(account.company);
  await page.getByLabel("Email").fill(account.email);
  await page.getByLabel("Password").fill(account.password);
  // "Your name" is left blank on purpose: the schema allows a nameless owner.
  await page.getByRole("button", { name: "Create account" }).click();
}
