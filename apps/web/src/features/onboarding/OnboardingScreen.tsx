import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { CopyButton } from "@/shared/components/CopyButton";
import { useProjects } from "@/shared/projects";
import { forgetFirstSetup, readFirstSetup } from "@/shared/session";

import { useFirstTrace } from "./api";

/**
 * Two environment variables, then wait for the first span and say so out loud.
 *
 * The variables are the standard OTel ones, because that is what a customer already has
 * in their deploy config and the demo app in tools/ is configured by nothing else. The
 * key is shown here once — the API never returns it again.
 */

const KEY_HEADER = "x-nexovue-key";
/** Stands in for the real key when this screen is reached without one to show. */
const KEY_PLACEHOLDER = "nvk_live_…";

export function OnboardingScreen() {
  // Read once: this is a handoff, not a value that changes while the screen is open.
  const [setup] = useState(readFirstSetup);
  const projects = useProjects();

  const projectId = setup?.projectId ?? projects.data?.[0]?.id;
  const firstTrace = useFirstTrace(projectId);
  const arrived = firstTrace.data;

  const endpointLine = `OTEL_EXPORTER_OTLP_ENDPOINT=${setup?.otlpEndpoint ?? ""}`;
  const headersLine = `OTEL_EXPORTER_OTLP_HEADERS=${KEY_HEADER}=${setup?.apiKey ?? KEY_PLACEHOLDER}`;

  return (
    <div className="mx-auto min-h-dvh max-w-2xl px-6 py-12">
      <p className="font-semibold font-serif text-ink text-md tracking-tight">nexovue</p>

      <h1 className="mt-6 font-semibold font-serif text-2xl text-ink tracking-tight">
        Send your first span
      </h1>
      <p className="mt-2 max-w-prose text-ink-dim text-md leading-relaxed">
        Set these two variables wherever your service runs, then restart it. Anything already
        instrumented with OpenTelemetry needs nothing else.
      </p>

      <div className="mt-8 border-line border-t">
        <EnvironmentVariable line={endpointLine} label="endpoint variable" />
        <EnvironmentVariable line={headersLine} label="key variable" />
      </div>

      <div className="mt-3 flex items-center gap-3">
        <CopyButton value={`${endpointLine}\n${headersLine}`} label="both variables" />
        <p className="text-ink-dim text-xs">
          {setup
            ? "This key is shown once and is never retrievable again."
            : "The key was shown once when the account was created. Issue a new one under Settings → API keys."}
        </p>
      </div>

      <div className="mt-10 border-line border-t pt-6">
        {arrived ? (
          <Arrived
            traceId={arrived.trace_id}
            service={arrived.root_service}
            name={arrived.root_name}
          />
        ) : (
          <Waiting failed={firstTrace.isError} />
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

function Waiting({ failed }: { failed: boolean }) {
  return (
    <div role="status" aria-live="polite">
      <h2 className="font-medium font-serif text-ink text-lg tracking-tight">
        Listening for your first span
      </h2>
      <p className="mt-2 max-w-prose text-ink-dim text-sm leading-relaxed">
        {failed
          ? "We could not check just now. This page keeps trying, so leave it open."
          : "Nothing has arrived yet. This page checks on its own and will say so the moment something does — leave it open while your service starts up."}
      </p>
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
