import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { useCan } from "@/shared/identity";

/* F5 Step 7 (plan T7.7; BR14, D45, AC60, AC61): an owner's per-repository choices for draft
 * pull requests, on `/settings/ai`, each off until an owner sets it. Other roles read the
 * state without the controls. While write access is off in code until the founder's go
 * (ADR-097), the API's sentence says so above the choices. */

export interface RepositoryDraftSettings {
  repository_id: string;
  repository: string;
  is_private: boolean | null;
  drafts_allowed: boolean;
  ci_on_open: boolean;
  draft_refused_recently: boolean;
  set_at: string | null;
}

export interface RepositoryDraftSettingsView {
  available: boolean;
  writes_off: string | null;
  repositories: RepositoryDraftSettings[];
}

export const DRAFT_SETTINGS_WORDS = {
  heading: "Draft pull requests",
  lead: "Nexovue opens a draft only in a repository an owner allowed here. This is Nexovue's limit, not GitHub's: the App's write access reaches every repository it is installed on.",
  failed: "Draft pull request settings could not be loaded",
  allow: "Allow draft pull requests",
  ciOnOpen: "Run CI when a draft opens",
  ciOnOpenWarning:
    "CI then runs the model's code, possibly with this repository's secrets, before anyone reviews it.",
  refused: "GitHub refused a draft here in the last 24 hours.",
  on: "on",
  off: "off",
} as const;

const SETTINGS_KEY = ["connections", "fix-settings"] as const;

export function useDraftSettings() {
  return useQuery<RepositoryDraftSettingsView>({
    queryKey: SETTINGS_KEY,
    queryFn: ({ signal }) =>
      apiClient.get<RepositoryDraftSettingsView>("connections/github/repositories/fix-settings", {
        signal,
      }),
  });
}

function useChangeDraftSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["connections", "fix-settings", "change"],
    mutationFn: (change: { repositoryId: string; drafts_allowed: boolean; ci_on_open: boolean }) =>
      apiClient.put<RepositoryDraftSettings>(
        `connections/github/repositories/${encodeURIComponent(change.repositoryId)}/fix-settings`,
        { drafts_allowed: change.drafts_allowed, ci_on_open: change.ci_on_open },
      ),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: SETTINGS_KEY });
    },
  });
}

export function FixRepositorySettings() {
  const settings = useDraftSettings();
  const change = useChangeDraftSettings();
  const owner = useCan("consent_ai_disclosure");
  if (settings.isPending) return <SkeletonRows rows={2} label={DRAFT_SETTINGS_WORDS.heading} />;
  if (settings.isError) {
    return (
      <InlineFailure
        title={DRAFT_SETTINGS_WORDS.failed}
        error={settings.error}
        onRetry={() => void settings.refetch()}
      />
    );
  }
  const view = settings.data;
  if (!view.available || view.repositories.length === 0) return null;
  return (
    <section
      aria-labelledby="draft-settings"
      className="flex flex-col gap-3"
      data-testid="draft-settings"
    >
      <h3 id="draft-settings" className="font-medium text-ink text-sm">
        {DRAFT_SETTINGS_WORDS.heading}
      </h3>
      <p className="max-w-prose text-ink-dim text-sm">{DRAFT_SETTINGS_WORDS.lead}</p>
      {view.writes_off ? <p className="max-w-prose text-ink text-sm">{view.writes_off}</p> : null}
      <ul className="flex flex-col gap-3">
        {view.repositories.map((repository) => (
          <li key={repository.repository_id} className="flex flex-col gap-1 text-sm">
            <span className="font-mono text-ink [overflow-wrap:anywhere]">
              {repository.repository}
            </span>
            {owner ? (
              <>
                <label className="flex min-h-11 items-center gap-2 text-ink">
                  <input
                    type="checkbox"
                    checked={repository.drafts_allowed}
                    disabled={change.isPending}
                    onChange={(event) =>
                      change.mutate({
                        repositoryId: repository.repository_id,
                        drafts_allowed: event.target.checked,
                        ci_on_open: repository.ci_on_open,
                      })
                    }
                  />
                  {DRAFT_SETTINGS_WORDS.allow}
                </label>
                <label className="flex min-h-11 items-center gap-2 text-ink">
                  <input
                    type="checkbox"
                    checked={repository.ci_on_open}
                    disabled={change.isPending}
                    onChange={(event) =>
                      change.mutate({
                        repositoryId: repository.repository_id,
                        drafts_allowed: repository.drafts_allowed,
                        ci_on_open: event.target.checked,
                      })
                    }
                  />
                  {DRAFT_SETTINGS_WORDS.ciOnOpen}
                </label>
                {repository.ci_on_open ? (
                  <p className="max-w-prose text-ink-dim">{DRAFT_SETTINGS_WORDS.ciOnOpenWarning}</p>
                ) : null}
              </>
            ) : (
              <span className="text-ink-dim">
                {DRAFT_SETTINGS_WORDS.allow}:{" "}
                {repository.drafts_allowed ? DRAFT_SETTINGS_WORDS.on : DRAFT_SETTINGS_WORDS.off} ·{" "}
                {DRAFT_SETTINGS_WORDS.ciOnOpen}:{" "}
                {repository.ci_on_open ? DRAFT_SETTINGS_WORDS.on : DRAFT_SETTINGS_WORDS.off}
              </span>
            )}
            {repository.draft_refused_recently ? (
              <p className="text-ink-dim">{DRAFT_SETTINGS_WORDS.refused}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
