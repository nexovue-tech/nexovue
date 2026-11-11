import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient, isForbidden } from "@/shared/api-client";
import { ASSISTANT_QUERY_ROOT } from "@/shared/assistant-context";

/** The API's closed words (`nexovue_platform/assistant_records.py`). */
export type StandingState = "ready" | "ai_off" | "chat_off" | "no_consent" | "opted_out";
export type MessageState =
  | "queued"
  | "running"
  | "answered"
  | "partial"
  | "abstained"
  | "withheld"
  | "stopped"
  | "refused"
  | "failed"
  | "over_limit"
  | "unavailable";
export type Phase = "waiting" | "asking" | "reading" | "checking";
export type Label = "counted" | "checked" | "text" | "unchecked";
export type WrongReason = "wrong_numbers" | "wrong_cause" | "missed_something" | "other";

export interface Availability {
  state: StandingState;
  provider_label: string | null;
  tested: boolean;
  questions_left_today: number | null;
  resets_at: string | null;
  waiting_for: string[];
  may_manage: boolean;
}

export interface StepLink {
  path: string;
  query: Record<string, string>;
  caveat: string | null;
}

export interface StepView {
  key: string;
  position: number;
  tool: string;
  words: string;
  display_arguments: string | null;
  search_hidden: boolean;
  from_time: string | null;
  to_time: string | null;
  row_count: number | null;
  was_cut: boolean;
  state: string;
  failure: string | null;
  link: StepLink | null;
}

export interface Sentence {
  text: string;
  label: Label;
  cause_warning: boolean;
  cites: string[];
}

export interface Answer {
  sentences: Sentence[];
  removed_sentence_count: number;
  not_verified: string[];
}

export interface MessageView {
  id: string;
  conversation_id: string;
  project_id: string;
  question: string;
  context: Record<string, unknown>;
  state: MessageState;
  phase: Phase | null;
  failure_reason: string | null;
  rejection_reason: string | null;
  abstain_reason: string | null;
  answer: Answer | null;
  wrong_reason: WrongReason | null;
  created_at: string;
  finished_at: string | null;
  steps: StepView[];
}

export interface ConversationSummary {
  id: string;
  project_id: string;
  title: string;
  last_message_at: string;
  in_flight_message_id: string | null;
}

export interface ConversationView {
  id: string;
  project_id: string;
  title: string;
  messages: MessageView[];
  has_older: boolean;
}

export interface AskContext {
  window?: { from: string; to: string } | null;
  finding_id?: string | null;
  service_name?: string | null;
  time_zone?: string | null;
}

/** How often a running question is read again (design, *Polling*: 1.5 s). */
export const POLL_MS = 1500;
/** Failed polls in a row before "Lost contact" (design, *States*). */
export const LOST_CONTACT_AFTER = 3;

const IN_FLIGHT: readonly MessageState[] = ["queued", "running"];

export function inFlight(state: MessageState): boolean {
  return IN_FLIGHT.includes(state);
}

const key = (...parts: unknown[]) => [ASSISTANT_QUERY_ROOT, ...parts] as const;

/**
 * A panel query never throws into the screen it sits beside: the central default throws a
 * 403 to the route boundary, which would render the whole screen forbidden (AC73).
 */
const PANEL_QUERY = { throwOnError: false, retry: false } as const;

export function useAvailability(projectId: string | undefined) {
  return useQuery({
    ...PANEL_QUERY,
    queryKey: key("availability", projectId),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<Availability>("assistant/availability", {
        signal,
        searchParams: { project_id: projectId },
      }),
  });
}

export function useConversations(projectId: string | undefined, enabled: boolean) {
  return useQuery({
    ...PANEL_QUERY,
    queryKey: key("conversations", projectId),
    enabled: enabled && projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ConversationSummary[]>("assistant/conversations", {
        signal,
        searchParams: { project_id: projectId },
      }),
  });
}

export function useConversation(conversationId: string | null) {
  return useQuery({
    ...PANEL_QUERY,
    queryKey: key("conversation", conversationId),
    enabled: conversationId !== null,
    queryFn: ({ signal }) =>
      apiClient.get<ConversationView>(`assistant/conversations/${conversationId}`, { signal }),
  });
}

/**
 * One question, read every 1.5 s while it is in flight, open panel or not (AC78). A 403
 * stops the polling (AC73); a failure keeps trying, and the panel says "Lost contact" after
 * three in a row (`failureCount`).
 */
export function useMessage(conversationId: string | null, messageId: string | null) {
  return useQuery({
    ...PANEL_QUERY,
    queryKey: key("message", messageId),
    enabled: conversationId !== null && messageId !== null,
    queryFn: ({ signal }) =>
      apiClient.get<MessageView>(
        `assistant/conversations/${conversationId}/messages/${messageId}`,
        {
          signal,
        },
      ),
    refetchInterval: (query) => {
      if (isForbidden(query.state.error)) return false;
      const data = query.state.data;
      return data && !inFlight(data.state) ? false : POLL_MS;
    },
    refetchIntervalInBackground: true,
  });
}

function useAssistantMutation<Input, Output>(run: (input: Input) => Promise<Output>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSettled: () => void queryClient.invalidateQueries({ queryKey: [ASSISTANT_QUERY_ROOT] }),
  });
}

export interface AskInput {
  projectId: string;
  conversationId: string | null;
  question: string;
  context: AskContext;
}

export function useAsk() {
  return useAssistantMutation(({ projectId, conversationId, question, context }: AskInput) =>
    conversationId === null
      ? apiClient.post<MessageView>("assistant/conversations", {
          project_id: projectId,
          question,
          context,
        })
      : apiClient.post<MessageView>(
          `assistant/conversations/${conversationId}/messages`,
          { question, context },
          { searchParams: { project_id: projectId } },
        ),
  );
}

export function useStop() {
  return useAssistantMutation((message: MessageView) =>
    apiClient.post<MessageView>(
      `assistant/conversations/${message.conversation_id}/messages/${message.id}/stop`,
    ),
  );
}

export function useMarkWrong() {
  return useAssistantMutation(
    ({ message, reason }: { message: MessageView; reason: WrongReason | null }) => {
      const path = `assistant/conversations/${message.conversation_id}/messages/${message.id}/feedback`;
      return reason === null
        ? apiClient.delete<void>(path)
        : apiClient.post<void>(path, { reason });
    },
  );
}

export function useDeleteConversation() {
  return useAssistantMutation((conversationId: string) =>
    apiClient.delete<void>(`assistant/conversations/${conversationId}`),
  );
}
