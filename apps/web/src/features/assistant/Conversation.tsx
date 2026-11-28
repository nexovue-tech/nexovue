import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { CopyButton } from "@/shared/components/CopyButton";

import { AnswerSentences } from "./AnswerSentences";
import { inFlight, type MessageView, useMarkWrong, useStop, type WrongReason } from "./api";
import { StepRow } from "./StepRow";
import {
  ASK_AGAIN,
  CHECK_IT_YOURSELF,
  COPY_STEPS,
  offersCheckItYourself,
  outcomeSentence,
  phaseLine,
  SLOW_WAIT,
  SLOW_WAIT_SECONDS,
  WRONG_MARKED,
} from "./states";
import { stepsAsText } from "./step-links";

const WRONG_REASONS: { value: WrongReason; label: string }[] = [
  { value: "wrong_numbers", label: "Wrong numbers" },
  { value: "wrong_cause", label: "Wrong cause" },
  { value: "missed_something", label: "Missed something" },
  { value: "other", label: "Something else" },
];

/** One question: the question, then the answer first and its steps folded, or live steps
 *  while it runs (design, *A conversation*). */
export function MessageBlock({
  message,
  zone,
  now,
  onAskAgain,
  onLeave,
}: {
  message: MessageView;
  zone: string | undefined;
  now: number;
  onAskAgain: (question: string) => void;
  onLeave?: () => void;
}) {
  const running = inFlight(message.state);
  const seconds = (now - new Date(message.created_at).getTime()) / 1000;
  const failedSteps = message.steps.filter((step) => step.state !== "read");
  const outcome = outcomeSentence(message);
  const stop = useStop();
  const cite = (key: string) => document.getElementById(`step-${key}`)?.focus();

  return (
    <article data-state={message.state} className="flex flex-col gap-3 border-line border-b py-3">
      <p className="max-w-prose whitespace-pre-wrap break-words font-medium text-ink text-sm">
        {message.question}
      </p>

      {running ? (
        <>
          <p role="status" className="text-ink-dim text-xs">
            {phaseLine(message.phase, seconds)}
          </p>
          {message.state === "queued" && seconds > SLOW_WAIT_SECONDS ? (
            <p className="text-ink-dim text-xs">
              {SLOW_WAIT}. <CheckItYourself message={message} onLeave={onLeave} />
            </p>
          ) : null}
          <ol>
            {message.steps.map((step) => (
              <StepRow key={step.key} step={step} zone={zone} now={now} onLeave={onLeave} />
            ))}
          </ol>
          <div>
            <Button
              variant="secondary"
              pending={stop.isPending}
              pendingLabel="Stopping…"
              onClick={() => stop.mutate(message)}
            >
              Stop
            </Button>
          </div>
        </>
      ) : (
        <>
          {message.answer && message.answer.sentences.length > 0 ? (
            <AnswerSentences
              answer={message.answer}
              messageId={message.id}
              onCite={cite}
              now={now}
              onLeave={onLeave}
            />
          ) : null}
          {outcome ? <p className="max-w-prose text-ink text-sm">{outcome}</p> : null}
          {offersCheckItYourself(message.state) ? (
            <p className="flex flex-wrap items-center gap-x-4 text-xs">
              <CheckItYourself message={message} onLeave={onLeave} />
              <button
                type="button"
                onClick={() => onAskAgain(message.question)}
                className="font-medium text-brand-text underline-offset-2 hover:underline"
              >
                {ASK_AGAIN}
              </button>
              <span className="font-mono text-ink-dim">Reference {message.id}</span>
            </p>
          ) : null}
          {failedSteps.length > 0 ? (
            <ol aria-label="Reads that failed">
              {failedSteps.map((step) => (
                <StepRow key={step.key} step={step} zone={zone} now={now} onLeave={onLeave} />
              ))}
            </ol>
          ) : null}
          {message.steps.length > 0 ? (
            <details>
              <summary className="cursor-pointer text-ink-dim text-xs">
                Read {message.steps.length} {message.steps.length === 1 ? "thing" : "things"}
              </summary>
              <ol>
                {message.steps.map((step) => (
                  <StepRow key={step.key} step={step} zone={zone} now={now} onLeave={onLeave} />
                ))}
              </ol>
            </details>
          ) : null}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {message.steps.length > 0 ? (
              <CopyButton
                label={COPY_STEPS}
                value={stepsAsText(message.steps, {
                  zone,
                  now,
                  origin: typeof window === "undefined" ? "" : window.location.origin,
                })}
              />
            ) : null}
            {message.answer ? <WrongControl message={message} /> : null}
          </div>
        </>
      )}
    </article>
  );
}

/** nexovue's own way to look: the finding the question was about, else the logs (design). */
function CheckItYourself({ message, onLeave }: { message: MessageView; onLeave?: () => void }) {
  const findingId = message.context.finding_id;
  const style = "font-medium text-brand-text underline-offset-2 hover:underline";
  return typeof findingId === "string" ? (
    <Link to="/findings/$findingId" params={{ findingId }} onClick={onLeave} className={style}>
      {CHECK_IT_YOURSELF}
    </Link>
  ) : (
    <Link to="/logs" onClick={onLeave} className={style}>
      {CHECK_IT_YOURSELF}
    </Link>
  );
}

function WrongControl({ message }: { message: MessageView }) {
  const mark = useMarkWrong();
  const [choosing, setChoosing] = useState(false);
  if (message.wrong_reason) {
    return (
      <p className="text-ink-dim text-xs">
        {WRONG_MARKED}{" "}
        <button
          type="button"
          onClick={() => mark.mutate({ message, reason: null })}
          className="font-medium text-brand-text underline-offset-2 hover:underline"
        >
          Undo
        </button>
      </p>
    );
  }
  if (!choosing) {
    return (
      <div>
        <button
          type="button"
          onClick={() => setChoosing(true)}
          className="text-ink-dim text-xs underline-offset-2 hover:underline"
        >
          This was wrong
        </button>
      </div>
    );
  }
  return (
    <fieldset className="flex flex-wrap gap-2 text-xs">
      <legend className="text-ink-dim">What was wrong?</legend>
      {WRONG_REASONS.map((reason) => (
        <button
          key={reason.value}
          type="button"
          onClick={() => {
            mark.mutate({ message, reason: reason.value });
            setChoosing(false);
          }}
          className="rounded border border-line px-2 py-1 text-ink"
        >
          {reason.label}
        </button>
      ))}
    </fieldset>
  );
}
