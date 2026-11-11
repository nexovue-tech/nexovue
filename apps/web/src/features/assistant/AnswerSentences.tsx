import type { Answer } from "./api";
import { CAUSE_WARNING, LABEL_WORDS, NOT_VERIFIED, REMOVED_ONE, removedMany } from "./states";

/**
 * A checked answer, **as plain text** (AC17): every model word goes through React's own
 * escaping, so Markdown and HTML reach the screen as the characters they are. Nothing here
 * sets HTML, follows a model link or draws an image. Each sentence carries its stored label
 * as text, in reading order, and numbered buttons that move focus to the steps it cites.
 */
export function AnswerSentences({
  answer,
  onCite,
}: {
  answer: Answer;
  onCite: (stepKey: string) => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      {answer.sentences.map((sentence) => (
        <div key={`${sentence.text}|${sentence.cites.join(",")}`} className="max-w-prose">
          <p className="whitespace-pre-wrap break-words text-ink text-sm">
            {sentence.text}{" "}
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
          <p className="text-ink-dim text-xs">{LABEL_WORDS[sentence.label]}</p>
          {sentence.cause_warning ? <p className="text-ink-dim text-xs">{CAUSE_WARNING}</p> : null}
        </div>
      ))}
      {answer.removed_sentence_count > 0 ? (
        <p className="max-w-prose text-ink-dim text-xs">
          {answer.removed_sentence_count === 1
            ? REMOVED_ONE
            : removedMany(answer.removed_sentence_count)}
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
