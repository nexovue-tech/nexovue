import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { apiClient } from "@/shared/api-client";
import { Button, buttonClass } from "@/shared/components/Button";
import { useCan, useSession } from "@/shared/identity";

import { type GetStartedStep, getStartedSteps, stepLink } from "./get-started";

/*
 * The other screens' reads, narrowed to what a step needs. A feature may not import another,
 * so these name the same endpoints; each is read only by someone who could act on it.
 */
interface RulesRead {
  rules: { enabled: boolean; channels: unknown[] }[];
}
interface DeploysRead {
  last_deploy_at: string | null;
}
interface ConnectionsRead {
  connections: { kind: string; state: string }[];
}
interface AiRead {
  on: boolean;
}

const HIDDEN_KEY = "nexovue.get-started.hidden";

/** A per-viewer convenience: hiding the box is remembered in this browser only. */
function useHidden(orgId: string | undefined): [boolean, () => void] {
  const key = `${HIDDEN_KEY}.${orgId ?? ""}`;
  const [hidden, setHidden] = useState(() => {
    try {
      return orgId !== undefined && window.localStorage.getItem(key) === "1";
    } catch {
      return false;
    }
  });
  return [
    hidden,
    () => {
      setHidden(true);
      try {
        window.localStorage.setItem(key, "1");
      } catch {
        // Hidden for this visit; the browser would not keep it.
      }
    },
  ];
}

/**
 * A new organization's first steps, above the feed (approved sketch H, 2026-11-28). Shown until
 * every step this person can do is done, or until they hide it.
 */
export function GetStarted({
  projectId,
  servicesReporting,
}: {
  projectId: string | undefined;
  servicesReporting: number | undefined;
}) {
  const session = useSession();
  const can = {
    deploys: useCan("configure_projects"),
    github: useCan("manage_connections"),
    ai: useCan("manage_ai"),
  };
  const [hidden, hide] = useHidden(session.data?.organization.id);
  const rules = useQuery({
    queryKey: ["alerts", "rules", projectId ?? ""] as const,
    enabled: projectId !== undefined && !hidden,
    queryFn: ({ signal }) =>
      apiClient.get<RulesRead>("alerts/rules", { searchParams: { project_id: projectId }, signal }),
  });
  const deploys = useQuery({
    queryKey: ["deploys", "settings", projectId ?? ""] as const,
    enabled: projectId !== undefined && can.deploys && !hidden,
    queryFn: ({ signal }) =>
      apiClient.get<DeploysRead>("deploys/webhook-secrets", {
        searchParams: { project_id: projectId },
        signal,
      }),
  });
  const connections = useQuery({
    queryKey: ["connections"] as const,
    enabled: can.github && !hidden,
    queryFn: ({ signal }) => apiClient.get<ConnectionsRead>("connections", { signal }),
  });
  const ai = useQuery({
    queryKey: ["ai", "connection"] as const,
    enabled: can.ai && !hidden,
    queryFn: ({ signal }) => apiClient.get<AiRead>("ai/connection", { signal }),
  });
  const user = session.data?.user;
  if (hidden || user === undefined || rules.isPending) return null;

  const view = getStartedSteps({
    servicesReporting,
    alertsReach: rules.data?.rules.some((rule) => rule.enabled && rule.channels.length > 0),
    emailVerified: user.email_verified_at !== null,
    deploysReported: deploys.data ? deploys.data.last_deploy_at !== null : undefined,
    githubConnected: connections.data?.connections.some(
      (each) => each.kind === "github" && each.state === "working",
    ),
    aiOn: ai.data?.on,
    can,
  });
  if (view.finished) return null;

  return (
    <section aria-labelledby="get-started" className="mx-4 mb-4 border border-line bg-surface">
      <header className="flex flex-wrap items-center justify-between gap-3 border-line border-b px-4 py-3">
        <div className="flex flex-col">
          <h2 id="get-started" className="heading-sub">
            Get started
          </h2>
          <p className="text-ink-dim text-xs">
            {view.done} of {view.steps.length} done
          </p>
        </div>
        <Button variant="quiet" onClick={hide}>
          Hide this
        </Button>
      </header>
      <ol>
        {view.steps.map((step, index) => (
          <li
            key={step.title}
            className={`grid grid-cols-[1.5rem_minmax(0,1fr)] items-center gap-x-3 gap-y-2 px-4 py-2.5 md:grid-cols-[1.5rem_minmax(0,1fr)_auto] ${index > 0 ? "border-line-subtle border-t" : ""}`}
          >
            <span
              aria-hidden="true"
              className={`grid size-5 place-items-center rounded-full border font-semibold text-2xs ${
                step.done
                  ? "border-brand bg-brand text-brand-contrast"
                  : "border-line-strong text-ink-dim"
              }`}
            >
              {step.done ? "✓" : index + 1}
            </span>
            <span className="flex min-w-0 flex-col">
              <span className="heading-sub">
                {step.title}
                <span className="sr-only">{step.done ? ", done" : ", not done"}</span>
              </span>
              <span className="text-ink-dim text-sm">{step.detail}</span>
            </span>
            <span className="col-start-2 md:col-start-auto">
              <StepAction step={step} />
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function StepAction({ step }: { step: GetStartedStep }) {
  const resend = useMutation({
    mutationFn: () => apiClient.post<void>("auth/verify-email/resend"),
  });
  if (step.place === "verify") {
    if (step.done) return null;
    return (
      <Button
        variant={step.next ? "primary" : "secondary"}
        pending={resend.isPending}
        pendingLabel="Sending"
        disabled={resend.isSuccess}
        onClick={() => resend.mutate()}
      >
        {resend.isSuccess ? "Sent: check your inbox" : "Send a new link"}
      </Button>
    );
  }
  const link = stepLink({ place: step.place, done: step.done });
  return (
    <Link
      to={link.to}
      className={
        step.next
          ? buttonClass("primary")
          : "inline-flex min-h-11 items-center font-medium text-brand-text text-sm underline-offset-2 hover:underline md:min-h-0"
      }
    >
      {link.words}
    </Link>
  );
}
