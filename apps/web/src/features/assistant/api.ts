import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient, isForbidden } from "@/shared/api-client";
import { ASSISTANT_QUERY_ROOT } from "@/shared/assistant-context";

/** The API's closed words (`nexovue_platform/assistant_records.py`). */
export type StandingState =
  | "ready"
  | "ai_off"
  | "chat_off"
  | "no_consent"
  | "opted_out"
  // Chat could answer, but the project has sent nothing to read yet.
  | "no_data";
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
/** `team`: the team's own words, with their age (F4 Step 5). */
export type Label = "counted" | "checked" | "text" | "team" | "unchecked";
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
  /** The console route's `$params`, filled by the API from the step's own arguments. */
  params: Record<string, string>;
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
  /** The first day still kept, on an aged-out step or a read narrowed to what is kept. */
  kept_since: string | null;
  /** How long a resolved case is kept, on a knowledge step: FR42's empty answer. */
  cases_kept_days?: number | null;
  link: StepLink | null;
}

export interface Sentence {
  text: string;
  label: Label;
  cause_warning: boolean;
  cites: string[];
  /** Character offsets `[start, end)` of each figure no cited number matched (FR16). */
  flagged_figures?: [number, number][];
  /** Quote ids, in the order the sentence named them. */
  quotes?: string[];
  /** On a `team` sentence, the oldest cited text's date, for the label's age (FR17). */
  written_at?: string | null;
}

/** What an answer stores of a quote: a reference, never the text (D28). */
export interface StoredQuote {
  step: string;
  tool: string;
  field: string;
  folded: boolean;
  /** A runbook's own text: the one quote a sentence may tell the reader to run (BR7). */
  runbook?: boolean;
}

export interface Answer {
  sentences: Sentence[];
  removed_sentence_count: number;
  not_verified: string[];
  quotes?: Record<string, StoredQuote>;
  /** Sentences removed for telling the reader to act on quoted text. */
  removed_for_acting_count?: number;
}

/** A quote read again from what is kept (`GET /assistant-quotes/{message_id}`). */
export interface QuoteView {
  id: string;
  tool: string;
  field: string;
  folded: boolean;
  state: "shown" | "gone" | "read_failed";
  value: string | null;
  was_cut: boolean;
  /** Read again, never taken from the stored answer: a runbook's quote, its runbook and age. */
  runbook?: boolean;
  runbook_id?: string | null;
  written_at?: string | null;
}

export interface QuoteList {
  message_id: string;
  quotes: QuoteView[];
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

/**
 * An answer's quotes, read again as the person when the answer is shown (design, *Quote
 * display*). Read once: they are records, not live values.
 */
export function useQuotes(messageId: string, enabled: boolean) {
  return useQuery({
    ...PANEL_QUERY,
    queryKey: key("quotes", messageId),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    queryFn: ({ signal }) => apiClient.get<QuoteList>(`assistant-quotes/${messageId}`, { signal }),
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
