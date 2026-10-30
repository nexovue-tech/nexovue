import { useState } from "react";

import { Tabs } from "@/shared/components/Tabs";
import { formatBinaryBytes } from "@/shared/format/bytes";
import { formatDuration } from "@/shared/format/duration";

import { COMPARISON_LABELS } from "./AssertionRows";
import type {
  CapturedBody,
  CapturedHeader,
  Comparison,
  RunEvidence,
  StepEvidence,
} from "./check-model";
import { LazyCodeEditor } from "./LazyCodeEditor";
import { outcomeUrgency, runSentence } from "./outcomes";

type EvidenceTab = "assertions" | "response" | "headers" | "request" | "timings";

const URGENCY_RULE = {
  critical: "border-l-critical-solid",
  warning: "border-l-warning-solid",
  none: "border-l-line",
} as const;

/**
 * What one run sent and received, and which assertion failed with what it found
 * (US18.15, FR31). The same view answers Run now and a recorded run, so a person learns
 * to read it once.
 */
export function EvidenceView({ evidence }: { evidence: RunEvidence }) {
  const failedAt = evidence.steps.findIndex((step) => !step.passed);
  const [selectedStep, setSelectedStep] = useState(Math.max(failedAt, 0));
  const step = evidence.steps[selectedStep];

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <p
        className={`border-l-2 py-1 pl-3 text-ink text-sm ${URGENCY_RULE[outcomeUrgency(evidence.outcome)]}`}
      >
        {runSentence(evidence)}
      </p>
      {/* A run that timed nothing contributes no duration here rather than an em
          dash: the facts either side of it read as a list, not as a gap. The location
          is last and never absent, so this line is never an empty paragraph. */}
      <p className="font-mono text-2xs text-ink-dim">
        {[
          evidence.duration_ms === null ? undefined : formatDuration(evidence.duration_ms),
          evidence.status_code === null ? undefined : String(evidence.status_code),
          evidence.tls_days_remaining === null
            ? undefined
            : `certificate ${evidence.tls_days_remaining} days`,
          evidence.maintenance ? "during maintenance" : undefined,
          evidence.final ? undefined : "overruled by a retry, not counted",
          `from ${evidence.location}`,
        ]
          .filter((fact) => fact !== undefined)
          .join(" · ")}
      </p>

      {evidence.steps.length > 1 ? (
        <ol aria-label="Requests in this run" className="flex flex-wrap gap-1">
          {evidence.steps.map((candidate, index) => (
            <li key={candidate.position}>
              <button
                type="button"
                aria-current={index === selectedStep ? "step" : undefined}
                onClick={() => setSelectedStep(index)}
                className={`rounded-sm border px-2 py-0.5 text-xs ${
                  index === selectedStep
                    ? "border-line-strong bg-raised text-ink"
                    : "border-line text-ink-dim"
                }`}
              >
                {index + 1}. {candidate.name || candidate.request?.method || "request"}{" "}
                <span
                  className={candidate.passed ? "text-ink-dim" : "font-medium text-critical-text"}
                >
                  {candidate.passed ? "passed" : "failed"}
                </span>
              </button>
            </li>
          ))}
        </ol>
      ) : null}

      {step ? <StepView key={step.position} step={step} /> : null}
    </div>
  );
}

function StepView({ step }: { step: StepEvidence }) {
  const [tab, setTab] = useState<EvidenceTab>(
    step.assertions.length > 0 ? "assertions" : "response",
  );
  const failed = step.assertions.filter((result) => !result.passed).length;
  const items = [
    { id: "assertions", label: "Assertions", count: step.assertions.length, invalid: failed > 0 },
    { id: "response", label: step.response ? "Response" : "Answer" },
    ...(step.response
      ? [{ id: "headers", label: "Headers", count: step.response.headers.length }]
      : []),
    ...(step.request ? [{ id: "request", label: "Request" }] : []),
    { id: "timings", label: "Timings" },
  ];

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <p className="truncate font-mono text-ink text-xs" title={step.target}>
        {step.request ? `${step.request.method} ` : ""}
        {step.target}
      </p>
      {step.error && step.assertions.every((result) => result.passed) ? (
        <p className="text-critical-text text-sm">{step.error}</p>
      ) : null}
      <Tabs
        label="Run evidence"
        items={items}
        selected={tab}
        onSelect={(id) => setTab(id as EvidenceTab)}
      >
        <div className="pt-3">
          {tab === "assertions" ? <AssertionResults step={step} /> : null}
          {tab === "response" ? <ResponseBody step={step} /> : null}
          {tab === "headers" && step.response ? (
            <HeaderList headers={step.response.headers} />
          ) : null}
          {tab === "request" && step.request ? (
            <div className="flex flex-col gap-3">
              <p className="break-all font-mono text-ink text-xs">
                {step.request.method} {step.request.url}
              </p>
              <HeaderList headers={step.request.headers} />
              <BodyText body={step.request.body} label="Request body" />
            </div>
          ) : null}
          {tab === "timings" ? <Timings step={step} /> : null}
        </div>
      </Tabs>
    </div>
  );
}

function AssertionResults({ step }: { step: StepEvidence }) {
  if (step.assertions.length === 0) {
    return (
      <p className="text-ink-dim text-sm">
        Nothing was asserted, or the request never got a response.
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-1">
      {step.assertions.map((result, index) => {
        const comparison =
          COMPARISON_LABELS[result.comparison as Comparison] ??
          result.comparison.replaceAll("_", " ");
        return (
          // Assertions have no identity beyond their position in a run that never changes.
          // biome-ignore lint/suspicious/noArrayIndexKey: a recorded run's rows are fixed
          <li key={index} className="flex items-baseline gap-2 text-sm">
            <span
              className={`shrink-0 font-mono text-xs ${result.passed ? "text-ink-dim" : "font-medium text-critical-text"}`}
            >
              {result.passed ? "✓ passed" : "✗ failed"}
            </span>
            <span className="min-w-0 text-ink">
              <span className="font-mono">
                {result.property || result.source.replaceAll("_", " ")}
              </span>{" "}
              {comparison} <span className="font-mono">{result.target}</span>
              {!result.passed ? (
                <span className="text-ink-dim">
                  {" "}
                  — got <span className="font-mono text-ink">{result.actual ?? "nothing"}</span>
                </span>
              ) : null}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function ResponseBody({ step }: { step: StepEvidence }) {
  if (step.response) {
    return (
      <div className="flex flex-col gap-2">
        <p className="font-mono text-ink text-xs">Status {step.response.status_code}</p>
        <BodyText body={step.response.body} label="Response body" />
      </div>
    );
  }
  if (step.answers.length > 0) {
    return (
      <ul className="font-mono text-ink text-xs">
        {step.answers.map((answer) => (
          <li key={answer}>{answer}</li>
        ))}
      </ul>
    );
  }
  return <p className="text-ink-dim text-sm">{step.error ?? "No response arrived."}</p>;
}

function BodyText({ body, label }: { body: CapturedBody | null; label: string }) {
  if (!body) return <p className="text-ink-dim text-sm">No body.</p>;
  const language = looksLikeJson(body.text)
    ? "json"
    : body.text.trimStart().startsWith("<")
      ? "xml"
      : "text";
  return (
    <div className="flex flex-col gap-1">
      {body.binary ? (
        <p className="text-ink-dim text-sm">{body.text}</p>
      ) : (
        <LazyCodeEditor
          label={label}
          value={body.text}
          language={language}
          readOnly
          maxLines={30}
        />
      )}
      <p className="text-ink-dim text-xs">
        {formatBinaryBytes(body.size_bytes)}
        {body.truncated ? " · cut off: the first 16 KiB is kept as evidence" : ""}
      </p>
    </div>
  );
}

function HeaderList({ headers }: { headers: readonly CapturedHeader[] }) {
  if (headers.length === 0) return <p className="text-ink-dim text-sm">No headers.</p>;
  return (
    <dl className="grid grid-cols-[minmax(0,12rem)_minmax(0,1fr)] gap-x-3 gap-y-0.5 font-mono text-xs">
      {headers.map((header, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: a header may repeat; its position is its identity
        <div key={index} className="contents">
          <dt className="truncate text-ink-dim">{header.name}</dt>
          <dd className="break-all text-ink">{header.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Timings({ step }: { step: StepEvidence }) {
  const phases = Object.entries(step.phases_ms);
  return (
    <dl className="grid max-w-xs grid-cols-2 gap-y-0.5 font-mono text-xs">
      <dt className="text-ink-dim">total</dt>
      <dd className="text-right text-ink">{formatDuration(step.duration_ms)}</dd>
      {phases.map(([phase, milliseconds]) => (
        <div key={phase} className="contents">
          <dt className="text-ink-dim">{phase}</dt>
          <dd className="text-right text-ink">{formatDuration(milliseconds)}</dd>
        </div>
      ))}
    </dl>
  );
}

function looksLikeJson(text: string): boolean {
  const start = text.trimStart()[0];
  return start === "{" || start === "[";
}
