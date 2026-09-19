/**
 * F26: a second person in an organization, end to end through the real stack.
 *
 * An owner invites a teammate; the invite email (rendered to the API's log in
 * development) carries the link; the teammate follows it, chooses a password and is in.
 * The owner then makes them a Viewer — their open session loses the controls on its next
 * request — and removes them, which signs them out.
 *
 * The invite link is read from `docker logs nexovue-api-1`, where the console mail
 * provider writes every rendered message: the same body a real provider would send.
 */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { type BrowserContext, expect, type Page, test } from "@playwright/test";

import { newAccount, signUp } from "./support/accounts";
import { takeAuthAllowance } from "./support/auth-quota";

const API_CONTAINER = "nexovue-api-1";
const MAIL_WAIT_MS = 15_000;
const LOG_BUFFER_BYTES = 64 * 1024 * 1024;
const TEAMMATE_PASSWORD = "correct horse battery staple";
// Built from the escape's code so the source holds no control character.
const ANSI_COLOUR = new RegExp(`${String.fromCharCode(27)}\\[[0-9;]*m`, "g");

/** The invite link mailed to `address`, from the console provider's log line. */
async function inviteLinkFor(address: string): Promise<string> {
  const deadline = Date.now() + MAIL_WAIT_MS;
  while (Date.now() < deadline) {
    // Two minutes, not more: after a load run the API log is large, and the default
    // 1 MB buffer overflows (ENOBUFS) long before the invite is found.
    const log = execFileSync("docker", ["logs", API_CONTAINER, "--since", "2m"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      maxBuffer: LOG_BUFFER_BYTES,
    });
    // structlog colours the fields; strip the escapes before reading them.
    const plain = log.replaceAll(ANSI_COLOUR, "");
    const line = plain
      .split("\n")
      .reverse()
      .find((each) => each.includes(`mail_to=${address}`) && each.includes("/invite?token="));
    const link = line?.match(/https?:\/\/[^\s'\\]+\/invite\?token=[A-Za-z0-9_-]+/)?.[0];
    if (link) return link;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`no invite mail to ${address} in ${API_CONTAINER}'s log`);
}

async function teammateSession(context: BrowserContext, link: string): Promise<Page> {
  const page = await context.newPage();
  // Preview and accept are both on the auth rate tier the suite's ledger governs.
  await takeAuthAllowance();
  await page.goto(new URL(link).pathname + new URL(link).search);
  await expect(page.getByRole("heading", { name: /^Join / })).toBeVisible();
  await page.getByLabel("Your name").fill("Priya");
  await page.getByLabel("Choose a password").fill(TEAMMATE_PASSWORD);
  await takeAuthAllowance();
  await page.getByRole("button", { name: /^Join / }).click();
  await expect(page).not.toHaveURL(/\/invite/);
  return page;
}

test("an owner brings a teammate in, lowers their role, and removes them", async ({
  page,
  browser,
}) => {
  const owner = newAccount();
  await signUp(page, owner);
  const teammate = `teammate-${randomUUID().slice(0, 8)}@example.test`;

  await page.goto("/settings/members");
  await page.getByLabel("Invite by email").fill(teammate);
  await page.getByLabel("As", { exact: true }).selectOption("member");
  await page.getByRole("button", { name: "Send invite" }).click();
  await expect(page.getByRole("region", { name: "Invite" }).getByRole("status")).toContainText(
    `Invited ${teammate}`,
  );
  const members = page.getByRole("list", { name: "Members" });
  await expect(members).toContainText("Invited");

  const link = await inviteLinkFor(teammate);
  const other = await browser.newContext();
  try {
    const priya = await teammateSession(other, link);

    // A member does the day-to-day: the way to add a check is there.
    await priya.goto("/synthetics");
    await expect(priya.getByRole("link", { name: "New check" }).first()).toBeVisible();

    // The owner sees them join.
    await page.reload();
    const row = members.getByRole("listitem").filter({ hasText: "Priya" });
    await expect(row).not.toContainText("Invited");

    // Lowered to Viewer: the teammate's open session loses the control on its next read.
    await row.getByLabel("Role", { exact: true }).selectOption("viewer");
    await expect(row.getByLabel("Role", { exact: true })).toHaveValue("viewer");
    await priya.reload();
    await expect(priya.getByRole("heading", { name: "Synthetics", level: 1 })).toBeVisible();
    await expect(priya.getByRole("link", { name: "New check" })).toHaveCount(0);

    // Removed: the next request signs them out.
    await row.getByRole("button", { name: "Remove" }).click();
    await row.getByLabel("Type Priya to confirm").fill("Priya");
    await row.getByRole("button", { name: "Remove Priya" }).click();
    await expect(members).not.toContainText("Priya");
    await priya.goto("/synthetics");
    await expect(priya).toHaveURL(/\/login/);
  } finally {
    await other.close();
  }
});
