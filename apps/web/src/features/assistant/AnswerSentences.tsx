import { Link } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";

import { CopyButton } from "@/shared/components/CopyButton";
import { writtenAge } from "@/shared/runbook-age";

import { type Answer, type QuoteView, type Sentence, useQuotes } from "./api";
import {
  CAUSE_WARNING,
  COPY_QUOTE,
  FIGURE_FLAG,
  LABEL_WORDS,
  NOT_VERIFIED,
  OPEN_RUNBOOK,
  QUOTE_CUT,
  QUOTE_FOLDED,
  QUOTE_GONE,
  QUOTE_LOADING,
  QUOTE_READ_FAILED,
  QUOTE_SOURCE,
  REMOVED_ONE,
  RUNBOOK_QUOTE_CUT,
  removedForActing,
  removedMany,
  runbookQuoteLabel,
  SHOW_FOLDED,
  teamLabel,
} from "./states";

/**
 * A checked answer, **as plain text** (AC17): every model word goes through React's own
 * escaping, so Markdown and HTML reach the screen as the characters they are. Nothing here
 * sets HTML, follows a model link or draws an image. Each sentence carries its stored label
 * as text, in reading order, its flagged figures where they stand (FR16, AC77), its quotes
 * read again by nexovue, and numbered buttons that move focus to the steps it cites. A
 * runbook's quote is the one labelled as the team's own, offered for copy, never folded
 * (BR7, FR15); `now` dates it.
 */
export function AnswerSentences({
  answer,
  messageId,
  onCite,
  now,
  onLeave,
}: {
  answer: Answer;
  messageId: string;
  onCite: (stepKey: string) => void;
  now: number;
  onLeave?: () => void;
}) {
  const quoted = Object.keys(answer.quotes ?? {}).length > 0;
  const quotes = useQuotes(messageId, quoted);
  const found = new Map((quotes.data?.quotes ?? []).map((quote) => [quote.id, quote]));
  const removedForActingCount = answer.removed_for_acting_count ?? 0;
  return (
    <div className="flex flex-col gap-3">
      {answer.sentences.map((sentence) => (
        <div key={`${sentence.text}|${sentence.cites.join(",")}`} className="flex flex-col gap-1">
          <p className="max-w-prose whitespace-pre-wrap break-words text-ink text-sm">
            <SentenceText sentence={sentence} />{" "}
            {sentence.cites.map((cite) => (
              <button
                key={cite}
                type="button"
                onClick={() => onCite(cite)}
                aria-label={`Open step ${cite}`}
                className="font-mono text-brand-text text-xs underline-offset-2 hover:underline"
              >
                [{cite}]
              </button>
            ))}
          </p>
          <p className="max-w-prose text-ink-dim text-xs">{labelOf(sentence, now)}</p>
          {sentence.cause_warning ? (
            <p className="max-w-prose text-ink-dim text-xs">{CAUSE_WARNING}</p>
          ) : null}
          {(sentence.quotes ?? []).map((id) => (
            <Quote
              key={id}
              tool={answer.quotes?.[id]?.tool ?? ""}
              folded={answer.quotes?.[id]?.folded ?? true}
              read={found.get(id)}
              loading={quotes.isPending}
              failed={quotes.isError}
              onRetry={() => void quotes.refetch()}
              now={now}
              onLeave={onLeave}
            />
          ))}
        </div>
      ))}
      {answer.removed_sentence_count > 0 ? (
        <p className="max-w-prose text-ink-dim text-xs">
          {answer.removed_sentence_count === 1
            ? REMOVED_ONE
            : removedMany(answer.removed_sentence_count)}
        </p>
      ) : null}
      {removedForActingCount > 0 ? (
        <p className="max-w-prose text-ink-dim text-xs">
          {removedForActing(removedForActingCount)}
        </p>
      ) : null}
      {answer.not_verified.length > 0 ? (
        <div className="max-w-prose">
          <p className="font-medium text-ink text-xs">{NOT_VERIFIED}</p>
          <ul className="list-disc pl-5 text-ink-dim text-xs">
            {answer.not_verified.map((line) => (
              <li key={line} className="break-words">
                {line}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/** A sentence's label in nexovue's words; the team's carries its age (design, *Words*). */
function labelOf(sentence: Sentence, now: number): string {
  if (sentence.label === "team") return teamLabel(writtenAge(sentence.written_at, now).words);
  return LABEL_WORDS[sentence.label] ?? LABEL_WORDS.unchecked;
}

/** A sentence's words, a flag beside each figure the worker could not match, where it was
 *  stored (FR16). Offsets outside the text are ignored rather than trusted. */
function SentenceText({ sentence }: { sentence: Sentence }) {
  const flags = (sentence.flagged_figures ?? [])
    .filter(([start, end]) => start >= 0 && end > start && end <= sentence.text.length)
    .sort((a, b) => a[0] - b[0]);
  const pieces: ReactNode[] = [];
  let at = 0;
  for (const [start, end] of flags) {
    if (start < at) continue;
    pieces.push(sentence.text.slice(at, start));
    pieces.push(
      <span key={start}>
        <mark className="bg-transparent text-ink underline decoration-dotted">
          {sentence.text.slice(start, end)}
        </mark>
        <span className="text-ink-dim text-xs"> ({FIGURE_FLAG})</span>
      </span>,
    );
    at = end;
  }
  pieces.push(sentence.text.slice(at));
  return <>{pieces}</>;
}

/**
 * A quote's value, read again by nexovue (design, *Quote display*): inert text in a bordered
 * block, never a link, with control and direction characters shown as visible escapes
 * (AC63). One that reads as a link or a command is folded under the warning.
 */
function Quote({
  tool,
  folded,
  read,
  loading,
  failed,
  onRetry,
  now,
  onLeave,
}: {
  tool: string;
  folded: boolean;
  read: QuoteView | undefined;
  loading: boolean;
  failed: boolean;
  onRetry: () => void;
  now: number;
  onLeave?: () => void;
}) {
  const [opened, setOpened] = useState(false);
  const source = QUOTE_SOURCE[tool] ?? "From what it read";
  if (loading) return <p className="text-ink-dim text-xs">{QUOTE_LOADING}</p>;
  if (failed || read?.state === "read_failed") {
    return (
      <p className="text-ink-dim text-xs">
        {QUOTE_READ_FAILED}.{" "}
        <button
          type="button"
          onClick={onRetry}
          className="font-medium text-brand-text underline-offset-2 hover:underline"
        >
          Retry
        </button>
      </p>
    );
  }
  if (!read || read.state === "gone" || read.value === null) {
    return <p className="text-ink-dim text-xs">{QUOTE_GONE[tool] ?? QUOTE_GONE.search_logs}</p>;
  }
  if (read.runbook) return <RunbookQuote read={read} now={now} onLeave={onLeave} />;
  // What nexovue read again decides, not what the stored answer claimed (T5.6).
  const isFolded = read.folded || folded;
  const shown = !isFolded || opened;
  return (
    <figure className="flex flex-col gap-1 border border-line px-2 py-1">
      <figcaption className="text-ink-dim text-xs">{source}</figcaption>
      {isFolded ? <p className="text-ink-dim text-xs">{QUOTE_FOLDED}</p> : null}
      {shown ? (
        <pre className="whitespace-pre-wrap break-words font-mono text-ink text-xs" inert>
          {visibleEscapes(read.value)}
        </pre>
      ) : (
        <button
          type="button"
          onClick={() => setOpened(true)}
          className="self-start text-brand-text text-xs underline-offset-2 hover:underline"
        >
          {SHOW_FOLDED}
        </button>
      )}
      {read.was_cut ? <p className="text-ink-dim text-xs">{QUOTE_CUT}</p> : null}
    </figure>
  );
}

/**
 * A runbook's own text (T5.6): labelled as the team's with its age, a warning tone past 180
 * days, never folded, with Copy; a cut one offers no copy and opens the runbook instead
 * (FR15). Still inert text, escaped like every other quote.
 */
function RunbookQuote({
  read,
  now,
  onLeave,
}: {
  read: QuoteView;
  now: number;
  onLeave?: () => void;
}) {
  const age = writtenAge(read.written_at, now);
  const value = read.value ?? "";
  return (
    <figure className="flex flex-col gap-1 border border-line px-2 py-1">
      <figcaption className={age.stale ? "text-warning-text text-xs" : "text-ink-dim text-xs"}>
        {runbookQuoteLabel(age.words)}
      </figcaption>
      <pre className="whitespace-pre-wrap break-words font-mono text-ink text-xs" inert>
        {visibleEscapes(value)}
      </pre>
      <p className="flex flex-wrap items-center gap-x-4 text-xs">
        {read.was_cut ? (
          <span className="text-ink-dim">{RUNBOOK_QUOTE_CUT}</span>
        ) : (
          <CopyButton label={COPY_QUOTE} value={value} />
        )}
        {read.runbook_id ? (
          <Link
            to="/settings/runbooks/$runbookId"
            params={{ runbookId: read.runbook_id }}
            onClick={onLeave}
            className="font-medium text-brand-text underline-offset-2 hover:underline"
          >
            {OPEN_RUNBOOK}
          </Link>
        ) : null}
      </p>
    </figure>
  );
}

// Control, bidirectional and invisible characters: shown as an escape such as \u{202E}, never acted on (AC63).
// Built from code points, so this file holds none of the characters it names.
const HIDDEN_RANGES: readonly (readonly [number, number])[] = [
  [0x00, 0x08],
  [0x0b, 0x1f],
  [0x7f, 0x9f],
  [0xad, 0xad],
  [0x61c, 0x61c],
  [0x200b, 0x200f],
  [0x2028, 0x202e],
  [0x2060, 0x2069],
  [0xfeff, 0xfeff],
];

function isHidden(codePoint: number): boolean {
  return HIDDEN_RANGES.some(([low, high]) => codePoint >= low && codePoint <= high);
}

export function visibleEscapes(text: string): string {
  return Array.from(text, (character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return isHidden(codePoint) ? `\\u{${codePoint.toString(16).toUpperCase()}}` : character;
  }).join("");
}
