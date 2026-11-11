import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";

import { CopyButton } from "@/shared/components/CopyButton";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";

import type { DeployLineAddition, ExplanationSegment, FindingFixes } from "./api";
import { Segments } from "./ExplanationSection";
import { BRIEF_WORDS, earlierLink, SUGGESTED_FIX, showsSection } from "./fix-page";

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
 * *Suggested fix* (D54's fifth row): full width below the split, so a diff has the window.
 * **A role with no line renders no section and no heading** (AC37e), and the hosted
 * service never holds one (AC11a). Plan Step 1 gives no role a line yet.
 */
export function FixSection({ fixes }: { fixes: FindingFixes | undefined }) {
  if (fixes === undefined || !showsSection(fixes) || fixes.section === null) return null;
  const { heading, sentence } = fixes.section;
  return (
    <section
      id={SUGGESTED_FIX.anchor}
      aria-label={heading ?? SUGGESTED_FIX.heading}
      className="flex flex-col gap-2"
    >
      {heading ? <h2 className="font-medium font-serif text-base text-ink">{heading}</h2> : null}
      <p className="max-w-prose text-ink-dim text-sm">{sentence}</p>
    </section>
  );
}
