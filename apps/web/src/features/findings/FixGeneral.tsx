import { useState } from "react";

import { ApiError } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { SelectField } from "@/shared/components/SelectField";

import type { FindingGeneralFix, GeneralContent, GeneralFix, GeneralLanguage } from "./api";
import { useAskGeneralFix } from "./api";
import { Segments } from "./ExplanationSection";
import {
  askedAgo,
  GENERAL_WORDS,
  LANGUAGE_NAMES,
  restsOn,
  stateWords,
  tokensAndAge,
  triesLeft,
} from "./fix-page";

/**
 * F5's rung 2b (plan T2b.8; spec FR33, FR37; D70): the offer, and a general suggestion.
 * **The label comes first, word for word, in every state that shows content** (AC72). The
 * example is text in a code block, selectable, with **no copy control**: the label must be
 * read before anything is pasted (D70). The API checked it again and restored names as
 * segments, so nothing here can become a link.
 */
export function GeneralSuggestion({
  fix,
  now,
  zone,
}: {
  fix: GeneralFix;
  now: number;
  zone?: string;
}) {
  return (
    <article aria-labelledby={`general-${fix.id}`} className="flex min-w-0 flex-col gap-2">
      <h3 id={`general-${fix.id}`} className="font-medium text-ink text-sm">
        {fix.heading}
      </h3>
      {fix.content ? (
        <GeneralContentView content={fix.content} zone={zone} />
      ) : fix.sentence ? (
        <p className="max-w-prose text-ink-dim text-sm">{fix.sentence}</p>
      ) : null}
      {fix.state === "proposed" && fix.content ? (
        <p className="text-ink-dim text-xs">
          {tokensAndAge(fix.tokens_used, fix.requested_at, now)}
        </p>
      ) : null}
    </article>
  );
}

function GeneralContentView({ content, zone }: { content: GeneralContent; zone?: string }) {
  const codeLabel = `${GENERAL_WORDS.example}, ${LANGUAGE_NAMES[content.language]}`;
  const rests = restsOn(content.evidence.map((each) => each.number));
  return (
    <>
      <p data-testid="general-label" className="max-w-prose font-medium text-ink text-sm">
        {content.label}
      </p>
      <p className="max-w-prose text-ink-dim text-sm">{content.lead}</p>
      {content.earlier_reading ? (
        <p className="max-w-prose text-ink-dim text-sm">{content.earlier_reading}</p>
      ) : null}
      <p className="max-w-prose break-words text-ink text-sm">
        <Segments segments={content.summary} zone={zone} />
      </p>
      <ol
        aria-label={GENERAL_WORDS.steps}
        className="flex max-w-prose list-decimal flex-col gap-1 pl-5 text-ink text-sm"
      >
        {content.steps.map((step, index) => (
          // Steps are the model's checked words in order; their position is their identity.
          // biome-ignore lint/suspicious/noArrayIndexKey: a step has no id but its place.
          <li key={index} className="break-words">
            <Segments segments={step} zone={zone} />
          </li>
        ))}
      </ol>
      {/* A labelled, focusable region, so a long line scrolls by keyboard (NFR6a). */}
      <section
        aria-label={codeLabel}
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling region must take focus.
        tabIndex={0}
        className="min-w-0 overflow-x-auto rounded-sm border border-line"
      >
        <pre data-testid="general-code" className="px-3 py-2 font-mono text-ink text-xs">
          <code>{content.code}</code>
        </pre>
      </section>
      {rests ? <p className="text-ink-dim text-xs">{rests}</p> : null}
    </>
  );
}

/**
 * FR33: the language, picked from the fixed list (the shared `SelectField`), and the send
 * sentence naming the provider. Nothing is typed, and focus is never moved for the person.
 */
export function GeneralOfferForm({
  findingId,
  general,
}: {
  findingId: string;
  general: FindingGeneralFix;
}) {
  const offer = general.offer;
  const [language, setLanguage] = useState<GeneralLanguage | "">(offer?.preselected ?? "");
  const [needsLanguage, setNeedsLanguage] = useState(false);
  const ask = useAskGeneralFix(findingId);
  if (offer === null) return null;
  const choices: LanguageChoice[] = [
    { id: "", name: GENERAL_WORDS.chooseLanguage },
    ...offer.languages.map((each) => ({ id: each, name: LANGUAGE_NAMES[each] })),
  ];

  const send = () => {
    setNeedsLanguage(language === "");
    if (language !== "") ask.mutate(language);
  };

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <p className="max-w-prose text-ink text-sm">{offer.lead}</p>
      <div className="flex flex-wrap items-end gap-3">
        <SelectField
          stacked
          capped
          label={GENERAL_WORDS.language}
          value={language}
          options={choices}
          optionLabel={(choice) => choice.name}
          onChange={(id) => setLanguage(id as GeneralLanguage | "")}
        />
        <Button
          onClick={send}
          pending={ask.isPending}
          pendingLabel={GENERAL_WORDS.pending}
          className="min-h-11"
        >
          {offer.action}
        </Button>
        <span className="text-ink-dim text-xs">{triesLeft(offer.tries_left)}</span>
      </div>
      {needsLanguage ? <p className="text-ink text-sm">{GENERAL_WORDS.pickFirst}</p> : null}
      <p className="max-w-prose text-ink-dim text-sm [overflow-wrap:anywhere]">
        {offer.send_sentence}
      </p>
      {ask.error ? <p className="max-w-prose text-ink text-sm">{refusalOf(ask.error)}</p> : null}
    </div>
  );
}

interface LanguageChoice {
  id: GeneralLanguage | "";
  name: string;
}

/** A refused click says the API's own sentence; `api-client` read the status (AC67). */
function refusalOf(error: unknown): string {
  return error instanceof ApiError
    ? (error.problem?.detail ?? error.message)
    : GENERAL_WORDS.notLoaded;
}

export function EarlierSuggestions({
  earlier,
  now,
}: {
  earlier: FindingGeneralFix["earlier"];
  now: number;
}) {
  if (earlier.length === 0) return null;
  return (
    <section aria-label={GENERAL_WORDS.earlier} className="flex flex-col gap-1">
      <h3 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {GENERAL_WORDS.earlier}
      </h3>
      <ul className="flex flex-col gap-1 text-ink-dim text-sm">
        {earlier.map((each) => (
          <li key={each.id}>
            {stateWords(each.state)} · {askedAgo(each.requested_at, now)}
          </li>
        ))}
      </ul>
    </section>
  );
}
