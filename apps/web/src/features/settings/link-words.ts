/** The GitHub account link's return page, in words (F5 design, *GitHub account link*). */
export const LINK_WORDS = {
  title: "Connect your GitHub account",
  description: "Finishing the link GitHub started.",
  waiting: "Asking GitHub who you are",
  failed: "The link did not finish",
  back: "Go back",
} as const;

// Each reason an attempt can end with (`GITHUB_LINK_REASONS`), as a sentence.
const REASON_WORDS: Record<string, string> = {
  expired: "This link has expired. Nothing was changed. Start again from the finding.",
  refused_by_github: "GitHub refused the link. Nothing was changed.",
  github_unavailable: "GitHub did not answer. Nothing was changed. Try again.",
  key_unreadable:
    "Nexovue could not open what it needs to finish the link. Nothing was changed. Tell an admin.",
};
const DID_NOT_FINISH = "GitHub has not answered yet. Go back and try again.";
const SOMETHING_FAILED = "The link did not finish. Nothing was changed. Try again.";

/**
 * What a polled attempt comes to: `linked`, a sentence to show, or null to keep waiting.
 * After the last poll a still-waiting attempt is said not to have finished.
 */
export function linkOutcome(
  status: string,
  reason: string | null,
  lastPoll: boolean,
): "linked" | string | null {
  if (status === "linked") return "linked";
  if (status === "refused" || status === "failed") {
    return (reason !== null ? REASON_WORDS[reason] : undefined) ?? SOMETHING_FAILED;
  }
  return lastPoll ? DID_NOT_FINISH : null;
}
