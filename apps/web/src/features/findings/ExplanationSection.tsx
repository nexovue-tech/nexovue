import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { FactLink, LookNextLinks } from "@/shared/components/LookNextLinks";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { factLink, lookNext, type WindowSource } from "@/shared/evidence-links";
import { formatZonedTime } from "@/shared/format/zoned";
import { useCan } from "@/shared/identity";
import { setupDocUrl } from "@/shared/setup-docs";

import type {
  ExplanationFact,
  ExplanationSegment,
  FindingDetail,
  FindingExplanation,
  RatingReason,
} from "./api";
import { useRateExplanation, useReadAgain } from "./api";
import type { HeaderLine } from "./explanation";
import {
  basisLabel,
  carriedNote,
  changeAnnouncement,
  FIRST_FACTS,
  listedFacts,
  RATING_WORDS,
  ratingLine,
  readAgainWait,
  readLine,
  refusedUntil,
  SECTION_HEADING,
  STILL_READING_AFTER_MS,
  setupStep,
  staleLine,
  WORDS,
} from "./explanation";

const LINK = "font-medium text-brand-text text-sm underline-offset-2 hover:underline";
/** Sentences are capped to a readable measure; the section itself uses the window. */
const PROSE = "max-w-prose";
/** Below `md`, a link is at least 44 pixels high (NFR4). */
const TAP = "inline-flex min-h-11 items-center md:min-h-0";

/**
 * Segments as text (design, *Segments*): a customer's name in the mono style, set apart
 * so it never reads as nexovue's words; a time in the project's zone. Nothing is ever
 * built from a value, so a name holding a URL or markup stays text (AC19).
 */
export function Segments({
  segments,
  zone,
}: {
  segments: readonly ExplanationSegment[];
  zone?: string;
}) {
  return (
    <>
      {segments.map((segment, index) => {
        const key = `${index}`;
        if (segment.at)
          return (
            <time key={key} dateTime={segment.at}>
              {formatZonedTime(segment.at, { zone })}
            </time>
          );
        if (segment.name != null) {
          return (
            <span key={key} className="font-mono text-ink [overflow-wrap:anywhere]">
              {segment.name}
            </span>
          );
        }
        return <span key={key}>{segment.text ?? ""}</span>;
      })}
    </>
  );
}

/**
 * The line under the title (FR14): always present, its space always held, so the
 * actions never move when reading becomes Likely (AC86).
 */
export function ExplanationHeaderLine({ line, zone }: { line: HeaderLine; zone?: string }) {
  return (
    <p data-testid="explanation-header" className={`min-h-[1.5rem] text-ink text-sm ${PROSE}`}>
      {line.kind === "loading" ? (
        <span className="text-ink-dim">{WORDS.reading}</span>
      ) : (
        <Segments segments={line.segments} zone={zone} />
      )}
      {line.kind === "cause" ? (
        <a href="#explanation-facts" className={`${LINK} ml-2`}>
          See the facts
        </a>
      ) : null}
    </p>
  );
}

/**
 * *What lines up with this* (F3 FR14, FR42, FR44, FR45, FR49; design, *Finding page*).
 * F28's *Release before this* is folded in as its first row (A45), and "Look next" sits
 * outside the explanation's own states, built from the finding, so it renders whether
 * the explanation loads, reads, fails or has expired (FR42, AC66).
 */
export function ExplanationSection({
  finding,
  explanation,
  error,
  onRetry,
  releaseRow,
  zone,
  sample = false,
}: {
  finding: FindingDetail;
  explanation: FindingExplanation | undefined;
  /** The query's error; with facts held, a failed refetch (design, *stale*). */
  error: unknown;
  onRetry: () => void;
  releaseRow: ReactNode;
  zone?: string;
  /**
   * F3 FR46: the labelled sample at `/findings/sample`, from fixed data. It offers no
   * action and no link, since nothing behind it exists, and makes no API call.
   */
  sample?: boolean;
}) {
  const announcement = useVersionAnnouncement(explanation);
  return (
    <section aria-labelledby="finding-explanation" className="flex flex-col gap-3">
      <h2 id="finding-explanation" className="font-medium font-serif text-base text-ink">
        {SECTION_HEADING}
      </h2>
      <p role="status" data-testid="explanation-status" className="sr-only">
        {announcement}
      </p>
      {releaseRow}
      <div className="flex flex-col gap-3">
        {sample ? null : (
          <div className="order-first md:order-none">
            <LookNext finding={finding} />
          </div>
        )}
        <Body
          finding={finding}
          explanation={explanation}
          error={error}
          onRetry={onRetry}
          zone={zone}
          sample={sample}
        />
      </div>
    </section>
  );
}

function Body({
  finding,
  explanation,
  error,
  onRetry,
  zone,
  sample,
}: {
  finding: FindingDetail;
  explanation: FindingExplanation | undefined;
  error: unknown;
  onRetry: () => void;
  zone?: string;
  sample: boolean;
}) {
  const failed = error != null;
  // A failed refetch keeps what is held: `isError` is true then too (design, *stale*).
  if (explanation === undefined) {
    if (failed) {
      return <InlineFailure title={WORDS.notLoaded} error={error} onRetry={onRetry} />;
    }
    return <SkeletonRows rows={3} label={WORDS.reading} />;
  }
  if (explanation.state === "reading") {
    const late = Date.now() - Date.parse(finding.started_at) >= STILL_READING_AFTER_MS;
    return (
      <p className="text-ink-dim text-sm">
        {late ? `${WORDS.stillReading}. ${WORDS.busy}` : `${WORDS.reading}.`}
      </p>
    );
  }
  if (explanation.state === "not_read") {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-ink-dim text-sm">{WORDS.notRead}.</p>
        <ReadAgain finding={finding} explanation={explanation} zone={zone} />
      </div>
    );
  }
  const read = readLine(explanation, zone);
  return (
    <div className="flex flex-col gap-3">
      {read || failed ? (
        <p className="text-ink-dim text-sm" data-testid="explanation-read">
          {failed ? staleLine(explanation, zone) : read}
        </p>
      ) : null}
      {explanation.likely_cause?.basis === "verified" &&
      explanation.likely_cause.rule.includes("deploy") ? (
        <p className={`text-ink-dim text-sm ${PROSE}`}>{WORDS.signedTimingOnly}</p>
      ) : null}
      <Facts facts={listedFacts(explanation.facts)} finding={finding} zone={zone} links={!sample} />
      <SetupStep explanation={explanation} />
      {sample ? null : (
        <>
          <ReadAgain finding={finding} explanation={explanation} zone={zone} />
          <Rating finding={finding} explanation={explanation} />
        </>
      )}
    </div>
  );
}

/** What links and the window are built from: the finding as the page already has it. */
function windowSource(finding: FindingDetail): WindowSource {
  return {
    project_id: finding.project_id,
    finding_id: finding.id,
    started_at: finding.started_at,
    closed_at: finding.closed_at,
    raw_kept_until: finding.raw_kept_until ?? null,
  };
}

function Facts({
  facts,
  finding,
  zone,
  links,
}: {
  facts: ExplanationFact[];
  finding: FindingDetail;
  zone?: string;
  links: boolean;
}) {
  const [all, setAll] = useState(false);
  const firstHidden = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    if (all) firstHidden.current?.focus();
  }, [all]);
  if (facts.length === 0) return null;
  const shown = all ? facts : facts.slice(0, FIRST_FACTS);
  return (
    <div id="explanation-facts" className="flex flex-col gap-2">
      <ol className="flex flex-col gap-2">
        {shown.map((fact, index) => (
          <li
            key={fact.id}
            ref={index === FIRST_FACTS ? firstHidden : undefined}
            tabIndex={index === FIRST_FACTS ? -1 : undefined}
            className="grid gap-x-4 gap-y-1 text-sm md:grid-cols-[2rem_minmax(0,1fr)_14rem_minmax(0,14rem)]"
          >
            <span className="font-mono text-ink-dim">{fact.number}</span>
            <span className="min-w-0 text-ink [overflow-wrap:anywhere]">
              <Segments segments={fact.sentence} zone={zone} />
              {carriedNote(fact, zone) ? (
                <span className="block text-ink-dim">{carriedNote(fact, zone)}</span>
              ) : null}
            </span>
            {basisLabel(fact) ? (
              <span className="text-ink-dim">
                <span className="sr-only">Basis: </span>
                {basisLabel(fact)}
              </span>
            ) : (
              <span />
            )}
            {links ? <FactLinkCell fact={fact} finding={finding} /> : <span />}
          </li>
        ))}
      </ol>
      {!all && facts.length > FIRST_FACTS ? (
        <button type="button" className={`${LINK} ${TAP} self-start`} onClick={() => setAll(true)}>
          Show all {facts.length} facts
        </button>
      ) : null}
    </div>
  );
}

function SetupStep({ explanation }: { explanation: FindingExplanation }) {
  const step = setupStep(explanation);
  const canSetUp = useCan("manage_credentials");
  if (step === null) return null;
  return (
    <p className={`text-sm ${PROSE}`}>
      <a href={setupDocUrl(step.doc)} className={LINK}>
        {step.label}
      </a>
      {canSetUp ? null : <span className="text-ink-dim"> An owner or admin can set this up.</span>}
    </p>
  );
}

function ReadAgain({
  finding,
  explanation,
  zone,
}: {
  finding: FindingDetail;
  explanation: FindingExplanation;
  zone?: string;
}) {
  const may = useCan("request_explanation");
  const readAgain = useReadAgain(finding.id);
  if (!may) {
    return explanation.state === "not_read" ? (
      <p className="text-ink-dim text-sm">{WORDS.viewerRetry}</p>
    ) : null;
  }
  const refused = refusedUntil(readAgain.error);
  const wait = readAgainWait(refused ?? explanation.next_read_at, explanation.read_at, { zone });
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <Button
        variant="secondary"
        pending={readAgain.isPending}
        pendingLabel="Asking"
        disabled={wait !== null}
        onClick={() => readAgain.mutate()}
      >
        {WORDS.readAgain}
      </Button>
      {wait ? <span className="text-ink-dim text-sm">{wait}</span> : null}
    </div>
  );
}

function FactLinkCell({ fact, finding }: { fact: ExplanationFact; finding: FindingDetail }) {
  const entry = factLink(fact, windowSource(finding));
  if (entry === null) return <span />;
  return <FactLink entry={entry} projectId={finding.project_id} />;
}

/**
 * FR42: always here, built from the finding, in every state (AC66), each link at the
 * finding's window in the address (FR16, AC21).
 */
function LookNext({ finding }: { finding: FindingDetail }) {
  const entries = lookNext({
    ...windowSource(finding),
    service_name: finding.scope === "service" ? finding.service_name : null,
    signals: finding.signals,
  });
  return (
    <LookNextLinks entries={entries} projectId={finding.project_id} headingId="finding-look-next" />
  );
}

/**
 * FR20: "Was this useful?", for owners, admins and members, AI on or off; never a
 * viewer. A "No" takes a reason from the list the API offers for this version, so "the
 * likely cause is wrong" appears only with a stated cause (AC75). Carried to a later
 * version until the cause changes (AC83).
 */
function Rating({
  finding,
  explanation,
}: {
  finding: FindingDetail;
  explanation: FindingExplanation;
}) {
  const may = useCan("operate_alerts");
  const rate = useRateExplanation(finding.id);
  const [changing, setChanging] = useState(false);
  const [saying, setSaying] = useState<"no" | null>(null);
  if (!may || explanation.version === null) return null;
  const version = explanation.version;
  const mine = explanation.my_rating ?? null;
  const send = (helpful: boolean, reason: RatingReason | null) => {
    rate.mutate({ version, helpful, reason }, { onSuccess: () => setChanging(false) });
    setSaying(null);
  };
  if (mine !== null && !changing) {
    return (
      <p className="flex flex-wrap items-center gap-x-3 text-ink-dim text-sm">
        <span role="status">{ratingLine(mine)}</span>
        <button type="button" className={`${LINK} ${TAP}`} onClick={() => setChanging(true)}>
          Change
        </button>
      </p>
    );
  }
  return (
    <fieldset className="flex flex-col gap-1" disabled={rate.isPending}>
      <legend className="text-ink text-sm">{RATING_WORDS.question}</legend>
      <div className="flex flex-wrap items-center gap-x-3">
        <button type="button" className={`${LINK} ${TAP}`} onClick={() => send(true, null)}>
          {RATING_WORDS.yes}
        </button>
        <button type="button" className={`${LINK} ${TAP}`} onClick={() => setSaying("no")}>
          {RATING_WORDS.no}
        </button>
      </div>
      {saying === "no" ? (
        <ul className="flex flex-col gap-x-4 md:flex-row md:flex-wrap">
          {(explanation.rating_reasons ?? []).map((reason) => (
            <li key={reason}>
              <button
                type="button"
                className={`${LINK} ${TAP}`}
                onClick={() => send(false, reason)}
              >
                {RATING_WORDS.reasons[reason]}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </fieldset>
  );
}

function useVersionAnnouncement(explanation: FindingExplanation | undefined): string {
  const [message, setMessage] = useState("");
  const previous = useRef<FindingExplanation | undefined>(undefined);
  useEffect(() => {
    const said = changeAnnouncement(previous.current, explanation);
    previous.current = explanation;
    if (said !== null) setMessage(said);
  }, [explanation]);
  return message;
}
