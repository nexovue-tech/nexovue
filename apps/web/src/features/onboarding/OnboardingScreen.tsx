import { useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { Button } from "@/shared/components/Button";
import { CopyButton } from "@/shared/components/CopyButton";
import { useProjects } from "@/shared/projects";
import type { FirstSetup } from "@/shared/session";
import { forgetFirstSetup, readFirstSetup } from "@/shared/session";

import type { FirstTrace } from "./api";
import { useFirstTrace } from "./api";
import { SetupGuides } from "./SetupGuides";
import { OTLP_PROTOCOL } from "./setup-docs";
import { Waiting } from "./Waiting";

/**
 * Three environment variables, then wait for the first span and say so out loud.
 *
 * The variables are the standard OTel ones, because that is what a customer already has
 * in their deploy config and the demo app in tools/ is configured by nothing else. The
 * key is shown here once — the API never returns it again.
 */

const KEY_HEADER = "x-nexovue-key";
/** Stand in for the real values when this screen is reached without them to show. */
const KEY_PLACEHOLDER = "nvk_live_…";
const ENDPOINT_PLACEHOLDER = "https://…";

/**
 * Silence long enough that "leave it open" stops being the right advice. An SDK that is
 * working exports within seconds of the process starting, so two minutes means either
 * the service has not been restarted yet or something is misconfigured — and the same
 * list of checks answers both.
 */
const SILENCE_MS = 120_000;

export function OnboardingScreen() {
  // Read once: this is a handoff, not a value that changes while the screen is open.
  const [setup] = useState(readFirstSetup);
  const projects = useProjects();
  const silent = useSilence(SILENCE_MS);

  const projectId = setup?.projectId ?? projects.data?.[0]?.id;
  const firstTrace = useFirstTrace(projectId);

  return (
    <OnboardingView
      setup={setup}
      arrived={firstTrace.data}
      failed={firstTrace.isError}
      silent={silent}
    />
  );
}

/** True once `ms` have passed since the screen opened. */
function useSilence(ms: number): boolean {
  const [elapsed, setElapsed] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setElapsed(true), ms);
    return () => clearTimeout(timer);
  }, [ms]);

  return elapsed;
}

export interface OnboardingViewProps {
  setup: FirstSetup | undefined;
  arrived: FirstTrace | undefined;
  failed: boolean;
  silent: boolean;
}

export function OnboardingView({ setup, arrived, failed, silent }: OnboardingViewProps) {
  const endpoint = setup?.otlpEndpoint ?? ENDPOINT_PLACEHOLDER;
  const variables = [
    { line: `OTEL_EXPORTER_OTLP_ENDPOINT=${endpoint}`, label: "endpoint variable" },
    {
      line: `OTEL_EXPORTER_OTLP_HEADERS=${KEY_HEADER}=${setup?.apiKey ?? KEY_PLACEHOLDER}`,
      label: "key variable",
    },
    { line: `OTEL_EXPORTER_OTLP_PROTOCOL=${OTLP_PROTOCOL}`, label: "protocol variable" },
  ];

  return (
    <div className="mx-auto min-h-dvh max-w-2xl px-6 py-12">
      <p className="font-semibold font-serif text-ink text-md tracking-tight">nexovue</p>

      <h1 className="mt-6 font-semibold font-serif text-2xl text-ink tracking-tight">
        Send your first span
      </h1>
      <p className="mt-2 max-w-prose text-ink-dim text-md leading-relaxed">
        Set these three variables wherever your service runs, then restart it. Anything already
        instrumented with OpenTelemetry needs nothing else.
      </p>

      <div className="mt-8 border-line border-t">
        {variables.map(({ line, label }) => (
          <EnvironmentVariable key={label} line={line} label={label} />
        ))}
      </div>

      <div className="mt-3 flex items-center gap-3">
        <CopyButton value={variables.map(({ line }) => line).join("\n")} label="all three" />
        <p className="text-ink-dim text-xs">
          {setup
            ? "This key is shown once and is never retrievable again."
            : "The key is shown once, when the account is created. Issue a new one under Settings → API keys, and use the ingest endpoint your account was given."}
        </p>
      </div>

      <SetupGuides />

      <div className="mt-10 border-line border-t pt-6">
        {arrived ? (
          <Arrived
            traceId={arrived.trace_id}
            service={arrived.root_service}
            name={arrived.root_name}
          />
        ) : (
          <Waiting failed={failed} silent={silent} endpoint={endpoint} />
        )}
      </div>
    </div>
  );
}

function EnvironmentVariable({ line, label }: { line: string; label: string }) {
  return (
    <div className="flex items-center gap-3 border-line-subtle border-b py-2.5">
      <code className="min-w-0 flex-1 select-all break-all font-mono text-ink text-xs">{line}</code>
      <CopyButton value={line} label={label} />
    </div>
  );
}

function Arrived({ traceId, service, name }: { traceId: string; service: string; name: string }) {
  const navigate = useNavigate();

  // Setup is finished, so the key this screen was holding stops being needed.
  const openTrace = () => {
    forgetFirstSetup();
    void navigate({ to: "/traces/$traceId", params: { traceId }, replace: true });
  };

  // The two things Flow B found the Indie persona reaches for next, and the
  // reason the confirmation is the destination rather than the trace list: a
  // customer who has just proved it works is deciding what to do about it, not
  // reading spans.
  const leaveFor = (to: "/synthetics" | "/usage") => () => {
    forgetFirstSetup();
    void navigate({ to, replace: true });
  };

  return (
    <div role="status" aria-live="polite">
      <h2 className="font-medium font-serif text-ink text-lg tracking-tight">
        Your first span arrived.
      </h2>
      <p className="mt-2 text-ink-dim text-sm leading-relaxed">
        <span className="font-mono text-ink text-xs">{service}</span> reported{" "}
        <span className="text-ink">{name}</span>. Everything it calls will show up alongside it.
      </p>

      <div className="mt-5 flex flex-wrap gap-3">
        {/* The specific trace rather than the list: the tail sampler keeps a
            project's first traces unconditionally, so the one they just sent is
            there to be opened instead of sampled away before anyone looks. */}
        <Button onClick={openTrace}>See your trace</Button>
        <Button variant="secondary" onClick={leaveFor("/synthetics")}>
          Add an uptime check
        </Button>
        <Button variant="secondary" onClick={leaveFor("/usage")}>
          What this costs
        </Button>
      </div>
    </div>
  );
}
