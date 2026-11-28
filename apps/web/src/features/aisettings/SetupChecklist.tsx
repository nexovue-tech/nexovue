import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { apiClient } from "@/shared/api-client";
import { buttonClass } from "@/shared/components/Button";
import { SkeletonRows } from "@/shared/components/Skeleton";
import type { AISettings } from "./api";
import { useSourceConsent } from "./FixConsentPanel";
import { useDraftSettings } from "./FixRepositorySettings";
import { type SetupInput, type SetupPlace, type SetupStep, setupSteps } from "./setup";

/*
 * The GitHub screen's reads, narrowed to what the checklist needs. A feature may not import
 * another, so these name the same endpoints and the same query keys: a change saved there
 * refreshes this list too.
 */
interface GitHubConnections {
  connections: { kind: string; state: string; app_slug: string | null; repositories: unknown[] }[];
}
interface SuggestionsApp {
  state: string | null;
  repositories: unknown[];
}
interface CodeLocations {
  locations: unknown[];
  services_without_location: string[];
}

function useSetupReads() {
  const connections = useQuery({
    queryKey: ["connections"] as const,
    queryFn: ({ signal }) => apiClient.get<GitHubConnections>("connections", { signal }),
  });
  const app = useQuery({
    queryKey: ["connections", "fix-app"] as const,
    queryFn: ({ signal }) =>
      apiClient.get<SuggestionsApp>("connections/github/fix-app", { signal }),
  });
  const locations = useQuery({
    queryKey: ["code-locations"] as const,
    queryFn: ({ signal }) => apiClient.get<CodeLocations>("code-locations", { signal }),
  });
  return { connections, app, locations };
}

export const TAB_FOR: Record<Exclude<SetupPlace, "github">, string> = {
  provider: "provider",
  fixes: "fixes",
};

/**
 * Setup: every step from a provider to draft pull requests, done or not, with where to set
 * it (approved drawing D). Reads only; nothing here sends anything.
 */
export function SetupChecklist({ settings }: { settings: AISettings }) {
  const { connections, app, locations } = useSetupReads();
  const consent = useSourceConsent();
  const drafts = useDraftSettings();
  if (connections.isPending || app.isPending || locations.isPending || consent.isPending) {
    return <SkeletonRows rows={6} label="Loading what is set up" />;
  }
  const connection = settings.connection ?? null;
  const github = connections.data?.connections.find((each) => each.kind === "github") ?? null;
  const live = (consent.data?.repositories ?? []).map((repository) => ({
    name: repository.repository,
    folders: repository.consents.filter((each) => each.standing === "live").length,
  }));
  const allowed = (drafts.data?.repositories ?? []).filter((each) => each.drafts_allowed);
  const input: SetupInput = {
    provider:
      connection === null
        ? null
        : {
            host: connection.provider_host.replace(/^https?:\/\//, ""),
            model: connection.model,
            tested: connection.checked_ok === true && connection.test_current,
            on: settings.on,
          },
    issuesApp:
      github === null
        ? null
        : { state: github.state, slug: github.app_slug, repositories: github.repositories.length },
    codeApp:
      app.data === undefined
        ? null
        : { state: app.data.state, repositories: app.data.repositories.length },
    locations:
      locations.data === undefined
        ? null
        : {
            set: locations.data.locations.length,
            total:
              locations.data.locations.length + locations.data.services_without_location.length,
          },
    consentedFolders: live.reduce((sum, each) => sum + each.folders, 0),
    consentedRepositories: live.filter((each) => each.folders > 0).map((each) => each.name),
    draftRepositories: allowed.length,
    ciOn: allowed.some((each) => each.ci_on_open),
  };
  const steps = setupSteps(input);
  const left = steps.filter((step) => !step.done).length;
  return (
    <div className="flex flex-col gap-3">
      <ol className="border border-line">
        {steps.map((step, index) => (
          <li
            key={step.title}
            className={`grid grid-cols-[1.5rem_minmax(0,1fr)] items-center gap-x-3 gap-y-2 px-4 py-3 md:grid-cols-[1.5rem_minmax(0,1fr)_auto] ${index > 0 ? "border-line border-t" : ""}`}
          >
            <span
              aria-hidden="true"
              className={`grid size-5 place-items-center rounded-full border font-semibold text-2xs ${
                step.done
                  ? "border-brand bg-brand text-brand-contrast"
                  : "border-warning-solid text-warning-text"
              }`}
            >
              {step.done ? "✓" : index + 1}
            </span>
            <span className="flex min-w-0 flex-col gap-0.5">
              <span className="heading-sub">
                {step.title}
                <span className="sr-only">{step.done ? ", done" : ", not done"}</span>
              </span>
              <span className="text-ink-dim text-sm [overflow-wrap:anywhere]">{step.detail}</span>
            </span>
            <span className="col-start-2 md:col-start-auto">
              <StepLink step={step} />
            </span>
          </li>
        ))}
      </ol>
      <p className="max-w-prose text-ink-dim text-sm">
        {left === 0
          ? "Everything is set up. Each line stays as it is until a setting changes."
          : `${left} ${left === 1 ? "step is" : "steps are"} left. Each line turns done when its setting is saved; nothing here sends anything.`}
      </p>
    </div>
  );
}

const PLACE_WORDS: Record<SetupPlace, string> = {
  provider: "Provider",
  fixes: "Suggested fixes",
  github: "GitHub",
};

function StepLink({ step }: { step: SetupStep }) {
  const words = step.next ? `Set up ${step.title.toLowerCase()}` : PLACE_WORDS[step.where];
  const target =
    step.where === "github"
      ? ({ to: "/settings/connections" } as const)
      : ({ to: "/settings/ai", search: { tab: TAB_FOR[step.where] } } as const);
  if (step.next) {
    // A link that looks like the page's one filled button: it goes somewhere, it does not act.
    return (
      <Link {...target} className={buttonClass("primary")}>
        {words}
      </Link>
    );
  }
  return (
    <Link
      {...target}
      className="inline-flex min-h-11 items-center font-medium text-brand-text text-sm underline-offset-2 hover:underline md:min-h-0"
    >
      {words}
    </Link>
  );
}
