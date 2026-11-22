import { Link } from "@tanstack/react-router";
import { X } from "lucide-react";
import { type KeyboardEvent, type RefObject, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, isForbidden } from "@/shared/api-client";
import {
  type AssistantSubject,
  closeAssistant,
  setAssistantActivity,
  useAssistant,
} from "@/shared/assistant-context";
import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { Refused } from "@/shared/components/Refused";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatZonedTime } from "@/shared/format/zoned";
import { useOnlineStatus } from "@/shared/hooks/use-online-status";
import { useCan, useSession } from "@/shared/identity";
import { useProjectSelection } from "@/shared/projects";

import {
  type Availability,
  inFlight,
  LOST_CONTACT_AFTER,
  type MessageState,
  type MessageView,
  useAsk,
  useAvailability,
  useConversation,
  useConversations,
  useDeleteConversation,
  useMessage,
} from "./api";
import { MessageBlock } from "./Conversation";
import { startersFor, usePathname } from "./starters";
import {
  ANSWER_READY,
  DELETED,
  deleteWarning,
  EVERYTHING_ELSE_WORKS,
  LOST_CONTACT,
  SEND_DATA,
  STANDING_WORDS,
  waitingFor,
} from "./states";

/** The design's cap (`MAX_QUESTION_CHARACTERS`); a count appears near it. */
export const MAX_QUESTION = 2000;
const COUNT_FROM = 1800;
const CLOCK_MS = 1000;
const WIDE_WINDOW = "(min-width: 1024px)";
const COARSE_POINTER = "(pointer: coarse)";

/** The person's zone the question was sent with (FR3); the browser's when it named none. */
function zoneOf(context: Record<string, unknown>): string | undefined {
  return typeof context.time_zone === "string" ? context.time_zone : undefined;
}

function matches(query: string): boolean {
  return typeof window !== "undefined" && window.matchMedia?.(query).matches === true;
}

/** The API's own sentence for a refused ask, beside the box, with the draft kept (AC79). */
function refusalOf(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  return error.problem?.detail ?? error.message;
}

/**
 * Chat's panel (F4 T2.11). Mounted by the shell for every signed-in screen and only hidden
 * when closed, so a running question keeps being read and the button can say "Answer ready"
 * (AC78). Every query sets `throwOnError: false`, and the shell gives it its own error
 * boundary, so nothing here can take the screen beside it down (AC73).
 */
export function AssistantPanel() {
  const { open, subject, freshConversation } = useAssistant();
  const { projects, projectId } = useProjectSelection();
  const project = projects.find((each) => each.id === projectId);
  const session = useSession();
  const online = useOnlineStatus();
  const mayAsk = useCan("ask_assistant");

  const availability = useAvailability(open ? projectId : undefined);
  const conversations = useConversations(projectId, mayAsk);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messageId, setMessageId] = useState<string | null>(null);
  const conversation = useConversation(conversationId);
  const live = useMessage(conversationId, messageId);

  // A reload resumes the question in flight, with Stop (AC78).
  const inFlightSummary = conversations.data?.find((each) => each.in_flight_message_id);
  useEffect(() => {
    if (messageId !== null || !inFlightSummary?.in_flight_message_id) return;
    setConversationId(inFlightSummary.id);
    setMessageId(inFlightSummary.in_flight_message_id);
  }, [inFlightSummary, messageId]);

  // "Ask about this finding" always starts a new conversation (design, *The finding page*).
  useEffect(() => {
    if (freshConversation === 0) return;
    setConversationId(null);
    setMessageId(null);
  }, [freshConversation]);

  const [now, setNow] = useState(() => Date.now());
  const running = live.data !== undefined && inFlight(live.data.state);
  useEffect(() => {
    if (!running) return;
    const tick = setInterval(() => setNow(Date.now()), CLOCK_MS);
    return () => clearInterval(tick);
  }, [running]);

  // The button's words while the panel is closed, and one status line for readers.
  const previous = useRef<MessageState | undefined>(undefined);
  const [announcement, setAnnouncement] = useState("");
  useEffect(() => {
    const state = live.data?.state;
    if (state === undefined) return;
    if (inFlight(state)) setAssistantActivity("answering");
    else if (previous.current !== undefined && inFlight(previous.current)) {
      setAssistantActivity(open ? "idle" : "ready");
      const count = live.data?.answer?.sentences.length ?? 0;
      setAnnouncement(`${ANSWER_READY}, ${count} ${count === 1 ? "sentence" : "sentences"}`);
    }
    previous.current = state;
  }, [live.data, open]);
  const lastStep = live.data?.steps.at(-1)?.words;
  useEffect(() => {
    if (lastStep && running) setAnnouncement(lastStep);
  }, [lastStep, running]);

  const forbidden =
    isForbidden(availability.error) ||
    isForbidden(conversations.error) ||
    isForbidden(conversation.error) ||
    isForbidden(live.error);
  const [draft, setDraft] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  // Opening moves focus to the box (design). The box appears only once availability reads
  // ready, often after the panel opens, so the move waits for it, once per opening.
  const focusOwed = useRef(false);
  const composerShown = !forbidden && mayAsk && availability.data?.state === "ready";
  useEffect(() => {
    focusOwed.current = open;
  }, [open]);
  useEffect(() => {
    if (!open || !composerShown || !focusOwed.current || !box.current) return;
    box.current.focus();
    focusOwed.current = false;
  }, [open, composerShown]);

  const close = () => {
    closeAssistant();
    document.getElementById(ASSISTANT_BUTTON_ID)?.focus();
  };
  // A citation on a phone closes the panel, which keeps its conversation (design, Flow A).
  const leave = () => {
    if (!matches(WIDE_WINDOW)) closeAssistant({ forLink: true });
  };

  const messages = useMemo(() => {
    const stored = conversation.data?.messages ?? [];
    const fresh = live.data;
    if (!fresh) return stored;
    return stored.some((each) => each.id === fresh.id)
      ? stored.map((each) => (each.id === fresh.id ? fresh : each))
      : [...stored, fresh];
  }, [conversation.data, live.data]);

  // Failed polls since the last answer: React Query resets `failureCount` at every fetch,
  // so the count is kept from its error tally instead.
  const errorsAtLastAnswer = useRef(0);
  const answeredAt = live.dataUpdatedAt;
  // biome-ignore lint/correctness/useExhaustiveDependencies: re-read only when an answer arrives
  useEffect(() => {
    errorsAtLastAnswer.current = live.errorUpdateCount;
  }, [answeredAt]);
  const failedInARow = live.errorUpdateCount - errorsAtLastAnswer.current;
  const lostContact = !forbidden && live.isError && failedInARow >= LOST_CONTACT_AFTER;

  return (
    <aside
      aria-label="Ask about your telemetry"
      hidden={!open}
      onKeyDown={(event) => {
        if (event.key === "Escape") close();
      }}
      className="fixed inset-0 z-40 flex min-h-0 flex-col bg-surface lg:static lg:z-auto lg:w-[clamp(360px,30vw,640px)] lg:shrink-0 lg:border-line lg:border-l"
    >
      <header className="flex min-h-11 items-center gap-2 border-line border-b px-3">
        <p className="min-w-0 flex-1 truncate font-medium text-ink text-sm">
          {project?.name ?? "—"}
        </p>
        <button
          type="button"
          onClick={() => {
            setConversationId(null);
            setMessageId(null);
          }}
          className="min-h-11 px-2 text-ink-dim text-xs hover:text-ink"
        >
          New
        </button>
        <button
          type="button"
          onClick={close}
          aria-label="Close"
          className="flex size-11 items-center justify-center text-ink-dim hover:text-ink"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </header>

      <p role="status" className="sr-only">
        {announcement}
      </p>

      {online ? null : (
        <p
          role="status"
          className="border-warning-solid border-b-2 bg-warning-subtle px-3 py-1.5 text-warning-text text-xs"
        >
          Offline — this question is not being read.
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-3">
        {forbidden ? (
          <div className="py-3">
            <Refused role={session.data?.user.role} who="an admin" />
          </div>
        ) : (
          <PanelBody
            availability={availability.data}
            availabilityPending={availability.isPending}
            availabilityError={availability.error}
            retryAvailability={() => void availability.refetch()}
            subject={subject}
            messages={messages}
            loadingConversation={conversation.isPending && conversationId !== null}
            conversationError={conversation.error}
            retryConversation={() => void conversation.refetch()}
            conversationId={conversationId}
            onDeleted={() => {
              setConversationId(null);
              setMessageId(null);
            }}
            now={now}
            onAskAgain={(question) => {
              setDraft(question);
              box.current?.focus();
            }}
            onStarter={(question) => {
              setDraft(question);
              // Focus Send, not the box: a mis-tap on a starter spends nothing.
              box.current?.form?.querySelector<HTMLButtonElement>("button[type=submit]")?.focus();
            }}
            onLeave={leave}
          />
        )}
        {lostContact ? (
          <p className="py-2 text-warning-text text-xs">
            {LOST_CONTACT}{" "}
            <button
              type="button"
              onClick={() => void live.refetch()}
              className="font-medium underline-offset-2 hover:underline"
            >
              Retry
            </button>
          </p>
        ) : null}
      </div>

      {composerShown ? (
        <Composer
          box={box}
          draft={draft}
          setDraft={setDraft}
          projectId={projectId}
          conversationId={conversationId}
          subject={subject}
          running={running}
          onAsked={(asked) => {
            setConversationId(asked.conversation_id);
            setMessageId(asked.id);
            setDraft("");
          }}
        />
      ) : null}
    </aside>
  );
}

export const ASSISTANT_BUTTON_ID = "assistant-button";

function PanelBody({
  availability,
  availabilityPending,
  availabilityError,
  retryAvailability,
  subject,
  messages,
  loadingConversation,
  conversationError,
  retryConversation,
  conversationId,
  onDeleted,
  now,
  onAskAgain,
  onStarter,
  onLeave,
}: {
  availability: Availability | undefined;
  availabilityPending: boolean;
  availabilityError: unknown;
  retryAvailability: () => void;
  subject: AssistantSubject | null;
  messages: MessageView[];
  loadingConversation: boolean;
  conversationError: unknown;
  retryConversation: () => void;
  conversationId: string | null;
  onDeleted: () => void;
  now: number;
  onAskAgain: (question: string) => void;
  onStarter: (question: string) => void;
  onLeave: () => void;
}) {
  if (availabilityError) {
    return (
      <div className="py-3">
        <InlineFailure
          title="Chat could not be read"
          error={availabilityError}
          onRetry={retryAvailability}
        />
      </div>
    );
  }
  if (availabilityPending || availability === undefined) return <SkeletonRows rows={3} />;
  if (availability.state !== "ready") {
    const sentence =
      availability.state === "no_consent" && !availability.may_manage
        ? waitingFor(availability.waiting_for)
        : STANDING_WORDS[availability.state](availability.may_manage);
    return (
      <div className="flex flex-col gap-2 py-4 text-sm" data-standing={availability.state}>
        <p className="max-w-prose text-ink">{sentence}</p>
        {availability.state === "no_data" ? (
          <Link
            to="/onboarding"
            className="font-medium text-brand-text text-sm underline-offset-2 hover:underline"
          >
            {SEND_DATA}
          </Link>
        ) : availability.may_manage && availability.state !== "opted_out" ? (
          <Link
            to="/settings/ai"
            className="font-medium text-brand-text text-sm underline-offset-2 hover:underline"
          >
            Open AI settings
          </Link>
        ) : null}
        <p className="max-w-prose text-ink-dim">{EVERYTHING_ELSE_WORKS}</p>
      </div>
    );
  }
  if (loadingConversation) return <SkeletonRows rows={4} />;
  if (conversationError) {
    return (
      <div className="py-3">
        <InlineFailure
          title="This conversation could not be read"
          error={conversationError}
          onRetry={retryConversation}
        />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2 py-2">
      {subject ? <p className="text-ink-dim text-xs">About {subject.headline}</p> : null}
      {messages.length === 0 ? (
        <Ready availability={availability} subject={subject} onStarter={onStarter} now={now} />
      ) : (
        messages.map((message) => (
          <MessageBlock
            key={message.id}
            message={message}
            zone={zoneOf(message.context)}
            now={now}
            onAskAgain={onAskAgain}
            onLeave={onLeave}
          />
        ))
      )}
      {conversationId !== null && messages.length > 0 ? (
        <DeleteConversation
          conversationId={conversationId}
          count={messages.length}
          onDeleted={onDeleted}
        />
      ) : null}
    </div>
  );
}

function Ready({
  availability,
  subject,
  onStarter,
  now,
}: {
  availability: Availability;
  subject: AssistantSubject | null;
  onStarter: (question: string) => void;
  now: number;
}) {
  const pathname = usePathname();
  return (
    <div className="flex max-w-prose flex-col gap-2 py-2 text-sm">
      <p className="text-ink">
        Chat reads this project's findings, services, releases, alerts, checks, errors, logs,
        traces, metrics and hosts, and cannot change anything.
      </p>
      <ul className="flex flex-col gap-1">
        {startersFor(subject, pathname).map((starter) => (
          <li key={starter}>
            <button
              type="button"
              onClick={() => onStarter(starter)}
              className="min-h-11 text-left text-brand-text underline-offset-2 hover:underline md:min-h-0"
            >
              {starter}
            </button>
          </li>
        ))}
      </ul>
      <p className="text-ink-dim text-xs">
        Answers go to {availability.provider_label ?? "your AI provider"},{" "}
        {availability.tested ? "tested" : "not tested"}.
      </p>
      {availability.questions_left_today !== null ? (
        <p className="text-ink-dim text-xs">
          {availability.questions_left_today} questions left today
          {availability.resets_at
            ? `; resets ${formatZonedTime(availability.resets_at, { now })}`
            : ""}
          .
        </p>
      ) : null}
    </div>
  );
}

function DeleteConversation({
  conversationId,
  count,
  onDeleted,
}: {
  conversationId: string;
  count: number;
  onDeleted: () => void;
}) {
  const remove = useDeleteConversation();
  const [confirming, setConfirming] = useState(false);
  if (remove.isSuccess) return <p className="text-ink-dim text-xs">{DELETED}</p>;
  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        className="self-start text-ink-dim text-xs underline-offset-2 hover:underline"
      >
        Delete conversation
      </button>
    );
  }
  return (
    <ConfirmPanel label="Delete this conversation">
      <p className="text-ink text-sm">{deleteWarning(count)}</p>
      {remove.isError ? (
        <p className="text-critical-text text-xs">{refusalOf(remove.error)}</p>
      ) : null}
      <div className="mt-2 flex gap-2">
        <Button
          pending={remove.isPending}
          pendingLabel="Deleting…"
          onClick={() => remove.mutate(conversationId, { onSuccess: onDeleted })}
        >
          Delete
        </Button>
        <Button variant="secondary" onClick={() => setConfirming(false)}>
          Keep it
        </Button>
      </div>
    </ConfirmPanel>
  );
}

function Composer({
  box,
  draft,
  setDraft,
  projectId,
  conversationId,
  subject,
  running,
  onAsked,
}: {
  box: RefObject<HTMLTextAreaElement | null>;
  draft: string;
  setDraft: (value: string) => void;
  projectId: string | undefined;
  conversationId: string | null;
  subject: AssistantSubject | null;
  running: boolean;
  onAsked: (asked: MessageView) => void;
}) {
  const ask = useAsk();
  const send = () => {
    const question = draft.trim();
    if (!question || !projectId || running) return;
    ask.mutate(
      {
        projectId,
        conversationId,
        question,
        context: {
          finding_id: subject?.findingId ?? null,
          service_name: subject?.serviceName ?? null,
          time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        },
      },
      { onSuccess: onAsked },
    );
  };
  // On a pointer device Enter sends and Shift+Enter breaks a line; on touch Enter breaks a
  // line; while an input method composes, Enter never sends (design, *The composer*).
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    if (matches(COARSE_POINTER)) return;
    event.preventDefault();
    send();
  };
  const refusal = ask.isError ? refusalOf(ask.error) : null;
  return (
    <form
      className="flex flex-col gap-1 border-line border-t p-3"
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <label htmlFor="assistant-question" className="sr-only">
        Your question
      </label>
      <div className="flex items-end gap-2">
        <textarea
          id="assistant-question"
          ref={box}
          value={draft}
          maxLength={MAX_QUESTION}
          rows={2}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          className="min-h-11 min-w-0 flex-1 resize-y rounded border border-line bg-page px-2 py-1.5 text-ink text-sm"
        />
        <Button
          type="submit"
          pending={ask.isPending}
          pendingLabel="Sending…"
          disabled={running || draft.trim() === ""}
        >
          Send
        </Button>
      </div>
      {draft.length >= COUNT_FROM ? (
        <p className="text-ink-dim text-xs">
          {draft.length} of {MAX_QUESTION} characters
        </p>
      ) : null}
      {refusal ? (
        <p role="alert" className="max-w-prose text-critical-text text-xs">
          {refusal}
        </p>
      ) : null}
    </form>
  );
}
