import { useSyncExternalStore } from "react";

import { useIsHosted } from "@/shared/deployment";
import { useCan, useIsOperator, useSession } from "@/shared/identity";

/**
 * What the person is looking at, for chat's panel (F4 design, *Context line*), and whether
 * the panel is open. Here, not in a feature, because a feature may not import another: a
 * screen sets its subject on mount and clears it on unmount, and the panel reads it.
 *
 * In memory only. A reload forgets the panel's open state; the in-flight question is found
 * again from the conversation list, not from here.
 */

/** Every assistant query's key starts here, so the refresh control can leave them out. */
export const ASSISTANT_QUERY_ROOT = "assistant";

export interface AssistantSubject {
  /** nexovue's own headline for the subject, never "this". */
  headline: string;
  findingId?: string;
  serviceName?: string;
}

/** What the TopBar's button says while the panel is closed (AC78). */
export type AssistantActivity = "idle" | "answering" | "ready";

export interface AssistantState {
  open: boolean;
  activity: AssistantActivity;
  /** On a phone, a step's link closed the panel: the button offers "Back to answer". */
  leftForLink: boolean;
  subject: AssistantSubject | null;
  /** Bumped by "Ask about this finding": the panel starts a new conversation. */
  freshConversation: number;
}

let state: AssistantState = {
  open: false,
  activity: "idle",
  leftForLink: false,
  subject: null,
  freshConversation: 0,
};
const listeners = new Set<() => void>();

function set(next: AssistantState): void {
  state = next;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useAssistant(): AssistantState {
  return useSyncExternalStore(subscribe, () => state);
}

export function openAssistant({ fresh = false }: { fresh?: boolean } = {}): void {
  set({
    ...state,
    open: true,
    leftForLink: false,
    // Opening shows the answer, so "Answer ready" has been read.
    activity: state.activity === "ready" ? "idle" : state.activity,
    freshConversation: fresh ? state.freshConversation + 1 : state.freshConversation,
  });
}

export function setAssistantActivity(activity: AssistantActivity): void {
  if (state.activity !== activity) set({ ...state, activity });
}

export function closeAssistant({ forLink = false }: { forLink?: boolean } = {}): void {
  set({ ...state, open: false, leftForLink: forLink });
}

export function setAssistantSubject(subject: AssistantSubject): void {
  set({ ...state, subject });
}

/** Clears only the subject it set, so a screen unmounting after the next one mounted
 *  cannot wipe its successor's. */
export function clearAssistantSubject(subject: AssistantSubject): void {
  if (state.subject === subject) set({ ...state, subject: null });
}

/** For tests: back to closed with no subject. */
export function resetAssistant(): void {
  set({ open: false, activity: "idle", leftForLink: false, subject: null, freshConversation: 0 });
}

/**
 * Whether to offer chat at all: the person may ask, it is not the operator or a support
 * view, who read no tenant's telemetry through it, and this install offers AI (the hosted
 * service does not, F3; F4 spec, *Error cases*: no panel). The API refuses regardless.
 */
export function useOffersAssistant(): boolean {
  const may = useCan("ask_assistant");
  const operator = useIsOperator();
  const supportView = useSession().data?.support_view != null;
  const hosted = useIsHosted();
  return may && !operator && !supportView && !hosted;
}
