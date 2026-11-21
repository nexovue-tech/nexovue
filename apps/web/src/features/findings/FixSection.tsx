import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";

import { CopyButton } from "@/shared/components/CopyButton";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";

import type {
  DeployLineAddition,
  ExplanationSegment,
  FindingFixes,
  FindingGeneralFix,
  FindingSourceFix,
} from "./api";
import { Segments } from "./ExplanationSection";
import { SourceSection } from "./FixFiles";
import { EarlierSuggestions, GeneralOfferForm, GeneralSuggestion } from "./FixGeneral";
import {
  BRIEF_WORDS,
  earlierLink,
  GENERAL_WORDS,
  SUGGESTED_FIX,
  showsSection,
  showsSource,
  stateWords,
} from "./fix-page";

const LINK = "font-medium text-brand-text text-sm underline-offset-2 hover:underline";

/**
 * F5's addition to F28's *Release before this* row (FR1): the version that ran before it,
 * with its release, and past F3's look-back, a sentence saying so. Drawn inside F28's
 * row, after F28's sentence: never a line or heading of its own.
 */
export function DeployLineExtras({
  addition,
  zone,
}: {
  addition: DeployLineAddition;
  zone?: string;
}) {
  const link = earlierLink(addition);
  return (
    <>
      {addition.sentences.map((sentence) => (
        <p key={sentenceKey(sentence)} className="break-words text-ink text-sm">
          <Segments segments={sentence} zone={zone} />
        </p>
      ))}
      {link ? (
        <Link to="/releases/$releaseId" params={{ releaseId: link.releaseId }} className={LINK}>
          <span className="[overflow-wrap:anywhere]">{link.label}</span>
        </Link>
      ) : null}
    </>
  );
}

/** Each sentence is a different fixed template, so its words are its key. */
function sentenceKey(sentence: readonly ExplanationSegment[]): string {
  return sentence.map((segment) => segment.text ?? segment.name ?? segment.at ?? "").join("");
}

/**
 * Rung 2 (FR5a): one line by default, directly under the deploy line. Copy, the warning
 * sentence, and a native disclosure holding the text, selectable, since a plain-HTTP
 * install has no clipboard. **When the clipboard write fails, the disclosure opens and
 * takes focus,** so there is something to select.
 */
export function FixBrief({
  fixes,
  pending,
  error,
  onRetry,
}: {
  fixes: FindingFixes | undefined;
  pending: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [focusText, setFocusText] = useState(false);
  const text = useRef<HTMLPreElement>(null);
  const showText = useCallback(() => {
    setOpen(true);
    setFocusText(true);
  }, []);
  // After the disclosure has rendered open, so the text is visible when it takes focus.
  useEffect(() => {
    if (!focusText || !open) return;
    text.current?.focus();
    setFocusText(false);
  }, [focusText, open]);

  if (fixes === undefined) {
    if (pending) return <SkeletonRows rows={1} label={BRIEF_WORDS.loading} />;
    return error == null ? null : (
      <InlineFailure title={BRIEF_WORDS.notLoaded} error={error} onRetry={onRetry} />
    );
  }
  if (fixes.brief === null) return null;
  return (
    <section aria-labelledby="finding-brief" className="flex flex-col gap-1">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h3 id="finding-brief" className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
          {BRIEF_WORDS.label}
        </h3>
        <p className="min-w-0 max-w-prose text-ink-dim text-sm">{BRIEF_WORDS.warning}</p>
        <CopyButton value={fixes.brief.text} label={BRIEF_WORDS.copied} onUnavailable={showText} />
      </div>
      <details
        open={open}
        onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
      >
        <summary className="cursor-pointer text-ink-dim text-sm hover:text-ink">
          {BRIEF_WORDS.disclosure}
        </summary>
        <pre
          ref={text}
          tabIndex={-1}
          data-testid="brief-text"
          className="mt-2 select-all whitespace-pre-wrap rounded-sm border border-line px-3 py-2 font-mono text-ink text-xs [overflow-wrap:anywhere]"
        >
          {fixes.brief.text}
        </pre>
      </details>
    </section>
  );
}

/**
 * *Suggested fix* (D54's fifth row): full width below the split, so content has the
 * window. Rung 2b's own read feeds it (`useGeneralFix`), apart from rungs 1 and 2. **A role
 * with no line, no offer and no suggestion renders no section and no heading** (AC37e,
 * FR10), and the hosted service never holds one (AC11a). Showing content wins over reasons
 * (FR10a): the latest suggestion first, then the reason's line and the offer, then older
 * ones. One always-present status line announces state changes; a poll moves neither focus
 * nor scroll (NFR6a).
 */
export function FixSection({
  findingId,
  general,
  source,
  error,
  onRetry,
  now,
  zone,
}: {
  findingId: string;
  general: FindingGeneralFix | undefined;
  /** Rung 3's own read (Step 3); absent while it loads, or where it failed. */
  source?: FindingSourceFix | undefined;
  error: unknown;
  onRetry: () => void;
  /** The caller's clock, so ages are pinned in a test. */
  now: number;
  zone?: string;
}) {
  if (general === undefined) {
    // Nothing while loading, so a role with no line never sees a heading flash (AC37e).
    return error == null ? null : (
      <section id={SUGGESTED_FIX.anchor} aria-label={SUGGESTED_FIX.heading}>
        <InlineFailure title={GENERAL_WORDS.notLoaded} error={error} onRetry={onRetry} />
      </section>
    );
  }
  const sourceShows = source !== undefined && showsSource(source);
  if (!showsSection(general) && !sourceShows) return null;
  const { line, latest } = general;
  const heading = line?.heading ?? SUGGESTED_FIX.heading;
  return (
    <section id={SUGGESTED_FIX.anchor} aria-label={heading} className="flex min-w-0 flex-col gap-3">
      <h2 className="font-medium font-serif text-base text-ink">{heading}</h2>
      <p role="status" data-testid="fix-status" className="sr-only">
        {latest ? stateWords(latest.state) : ""}
      </p>
      {latest ? <GeneralSuggestion fix={latest} now={now} zone={zone} /> : null}
      {line ? <p className="max-w-prose text-ink-dim text-sm">{line.sentence}</p> : null}
      {source && sourceShows ? (
        <SourceSection findingId={findingId} source={source} now={now} />
      ) : null}
      <GeneralOfferForm findingId={findingId} general={general} />
      <EarlierSuggestions earlier={general.earlier} now={now} />
    </section>
  );
}
