import { ApiError, problemCode } from "@/shared/api-client";

/**
 * Every sentence `/settings/runbooks` shows of its own (F4 design, `/settings/runbooks`;
 * FR32, AC90, AC92). One place, so a test holds each state to its words.
 */

export const TITLE = "Runbooks";
export const DESCRIPTION =
  "Your team's own instructions for handling problems, for this project. Chat can quote them in its answers.";
export const EMPTY = "No runbooks yet. Add one, and chat can find it.";
export const capReached = (limit: number) =>
  `This project holds ${limit} runbooks, the most it can. Delete one to add another.`;
export const FORMER_MEMBER = "Former member";
export const ADD = "Add a runbook";
export const UPLOAD = "Upload Markdown files";
export const SAVE = "Save";
export const SAVE_ANYWAY = "Save anyway";
export const UPLOAD_ANYWAY = "Upload these anyway";
export const EDIT = "Edit";
export const DELETE = "Delete";
export const DELETE_CONFIRM =
  "Chat will no longer find this. Earlier answers quoting it will show it is gone.";
export const DELETED = "Deleted.";
export const SUPPORT_NO_BODY = "A support view does not show what a runbook says.";
export const DO_NOT_PASTE_SECRETS = "Do not paste secrets.";
export const sentTo = (provider: string) =>
  `With chat on, this text may be sent to ${provider}. ${DO_NOT_PASTE_SECRETS}`;
export const MAX_BYTES = 32 * 1024;
export const TOO_LARGE = "A runbook can be at most 32 KiB.";
export const TITLE_NEEDED = "A runbook needs a title.";
export const TEXT_NEEDED = "A runbook needs some text.";
export const sizeLine = (bytes: number) => `${(bytes / 1024).toFixed(1)} of 32 KiB`;
/** Shown near the cap, as the chat box shows its count near its own (design). */
export const SIZE_SHOWN_FROM = MAX_BYTES * 0.8;
export const uploadedLine = (count: number) =>
  `${count} ${count === 1 ? "runbook" : "runbooks"} added.`;
export const CHOOSE_PROJECTS = "Add to these projects, each as its own copy";
export const NO_FILES = "Choose one or more Markdown files.";
export const NO_PROJECT = "Choose at least one project.";
export const CREDENTIAL_CODE = "looks_like_credential";

export function byteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** What the form refuses before sending, in the API's own words. */
export function localRefusal(title: string, content: string): string | null {
  if (!title.trim()) return TITLE_NEEDED;
  if (!content.trim()) return TEXT_NEEDED;
  if (byteLength(content) > MAX_BYTES) return TOO_LARGE;
  return null;
}

/** Whether the API asked for the second click on text that looks like a credential. */
export function asksForSecondClick(error: unknown): boolean {
  return problemCode(error) === CREDENTIAL_CODE;
}

/** The API's own sentence for a refusal (its problem's detail), else the error's words. */
export function refusalSentence(error: unknown): string {
  if (error instanceof ApiError) return error.problem?.detail ?? error.message;
  return error instanceof Error ? error.message : String(error);
}
