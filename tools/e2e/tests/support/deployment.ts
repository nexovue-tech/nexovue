import type { Page } from "@playwright/test";

/** Why a chat walk does not run against a hosted install: chat is not offered there. */
export const CHAT_NEEDS_SELF_HOSTED =
  "chat is not offered on a hosted install (foundation FD19), and the dev stack runs hosted; run it against a self-hosted stack";

/** Whether the console under test is a hosted install, as its API reports. */
export async function isHosted(page: Page): Promise<boolean> {
  const answer = await page.request.get("/api/v1/system/info");
  return ((await answer.json()) as { deployment?: string }).deployment === "hosted";
}
