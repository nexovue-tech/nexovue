import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { apiClient } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { useCan } from "@/shared/identity";
import { useProjectSelection } from "@/shared/projects";

/* F5 Step 3 (plan T3.5, T3.6): an owner's consent per repository and folder, and the
 * readiness counts, on `/settings/ai`. Other roles read the state without the controls. */

export interface SourceConsentView {
  id: string;
  folder_scope: string | null;
  provider_host: string;
  model: string;
  require_github_access: boolean;
  consented_at: string;
  standing: "live" | "ended" | "none";
}

export interface SourceConsentOverview {
  available: boolean;
  provider_host: string | null;
  model: string | null;
  region: string | null;
  requesters: number;
  repositories: {
    repository_id: string;
    repository: string;
    is_private: boolean;
    is_reachable: boolean;
    consents: SourceConsentView[];
  }[];
  sentences: string[];
  never_sent: string[];
}

export interface ReadinessCounts {
  findings: number;
  ready: number;
  ready_without_stack: number;
  cause_is_elsewhere: number;
  project_finding: number;
  closed_as_not_a_problem: number;
  services_without_location: string[];
  sentence: string;
}

export const CONSENT_WORDS = {
  heading: "Source consent",
  failed: "Source consent could not be loaded",
  empty: "No repositories connected yet. Set up the Nexovue suggestions App in Connections first.",
  wholeRepository: "the whole repository",
  agree: "Agree",
  confirmAgree: "Agree to send this code",
  withdraw: "Withdraw",
  folder: "Folder (blank for the whole repository)",
  question:
    "Require a linked GitHub account with read access to this repository to request a suggested fix, download a patch or read quoted code?",
  yes: "Yes, require it",
  no: "No",
  requesters: (count: number) => `${count} people can ask for a suggested fix today.`,
  live: "Agreed",
  ended: "Ended: the provider, key or model changed. Agree again, or withdraw.",
  to: (host: string, model: string) => `To ${host}, model ${model}.`,
  neverSent: "Never sent",
  readinessHeading: "How recent findings would reach a suggested fix",
  ready: (count: number) => `Ready from the error's stack: ${count}`,
  readyWithoutStack: (count: number) => `A person names the files: ${count}`,
  elsewhere: (count: number) => `Another service's finding first: ${count}`,
  noLocation: "Services with no code location",
} as const;

const CONSENT_KEY = ["connections", "source-consent"] as const;

export function useSourceConsent() {
  return useQuery<SourceConsentOverview>({
    queryKey: CONSENT_KEY,
    queryFn: ({ signal }) =>
      apiClient.get<SourceConsentOverview>("connections/github/repositories/source-consent", {
        signal,
      }),
  });
}

/** The one query parameter: a folder, percent-encoded, or the whole repository. */
export function scopeQuery(folder: string): string {
  const trimmed = folder.trim().replace(/^\/+|\/+$/g, "");
  return trimmed === "" ? "scope=whole-repository" : `folder=${encodeURIComponent(trimmed)}`;
}

function useAgree() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["connections", "source-consent", "agree"],
    mutationFn: (input: { repositoryId: string; folder: string; require: boolean }) =>
      apiClient.put<SourceConsentView>(
        `connections/github/repositories/${encodeURIComponent(input.repositoryId)}/source-consents?${scopeQuery(input.folder)}`,
        { require_github_access: input.require },
      ),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: CONSENT_KEY }),
  });
}

function useWithdraw() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["connections", "source-consent", "withdraw"],
    mutationFn: (input: { repositoryId: string; folder: string }) =>
      apiClient.delete<{ code_deleted: number }>(
        `connections/github/repositories/${encodeURIComponent(input.repositoryId)}/source-consents?${scopeQuery(input.folder)}`,
      ),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: CONSENT_KEY }),
  });
}

export function FixConsentPanel() {
  const { projectId } = useProjectSelection();
  return (
    <div className="flex flex-col gap-6">
      <FixReadinessPanel projectId={projectId ?? null} />
      <ConsentList />
    </div>
  );
}

function ConsentList() {
  const overview = useSourceConsent();
  const owner = useCan("consent_ai_disclosure");
  if (overview.isPending) return <SkeletonRows rows={2} label={CONSENT_WORDS.heading} />;
  if (overview.isError) {
    return (
      <InlineFailure
        title={CONSENT_WORDS.failed}
        error={overview.error}
        onRetry={() => void overview.refetch()}
      />
    );
  }
  const data = overview.data;
  if (!data.available || data.repositories.length === 0) {
    return <p className="max-w-prose text-ink-dim text-sm">{CONSENT_WORDS.empty}</p>;
  }
  return (
    <div className="flex flex-col gap-4" data-testid="fix-consent">
      <p className="text-ink-dim text-sm">{CONSENT_WORDS.requesters(data.requesters)}</p>
      {data.repositories.map((repository) => (
        <div key={repository.repository_id} className="flex flex-col gap-2">
          <h3 className="font-mono text-ink text-sm [overflow-wrap:anywhere]">
            {repository.repository}
          </h3>
          <ul className="flex flex-col gap-1 text-sm">
            {repository.consents.map((consent) => (
              <li key={consent.id} className="[overflow-wrap:anywhere]">
                {consent.folder_scope ?? CONSENT_WORDS.wholeRepository}:{" "}
                {consent.standing === "live" ? CONSENT_WORDS.live : CONSENT_WORDS.ended}{" "}
                {CONSENT_WORDS.to(consent.provider_host, consent.model)}
              </li>
            ))}
          </ul>
          {owner ? <AgreeForm repositoryId={repository.repository_id} overview={data} /> : null}
        </div>
      ))}
    </div>
  );
}

function AgreeForm({
  repositoryId,
  overview,
}: {
  repositoryId: string;
  overview: SourceConsentOverview;
}) {
  const agree = useAgree();
  const withdraw = useWithdraw();
  const [open, setOpen] = useState(false);
  const [folder, setFolder] = useState("");
  // D20: nothing preselected; the owner must answer.
  const [require, setRequire] = useState<boolean | null>(null);
  if (!open) {
    return (
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => setOpen(true)}>
          {CONSENT_WORDS.agree}
        </Button>
      </div>
    );
  }
  return (
    <div className="min-w-0">
      <ConfirmPanel label={CONSENT_WORDS.confirmAgree}>
        <TextField
          label={CONSENT_WORDS.folder}
          name="consent-folder"
          value={folder}
          onChange={setFolder}
        />
        <p className="max-w-prose text-ink text-sm">
          {CONSENT_WORDS.to(overview.provider_host ?? "—", overview.model ?? "—")}
        </p>
        {overview.sentences.map((sentence) => (
          <p key={sentence} className="max-w-prose text-ink text-sm">
            {sentence}
          </p>
        ))}
        <fieldset className="flex flex-col gap-1">
          <legend className="max-w-prose text-ink text-sm">{CONSENT_WORDS.question}</legend>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="require"
              checked={require === true}
              onChange={() => setRequire(true)}
            />
            {CONSENT_WORDS.yes}
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="require"
              checked={require === false}
              onChange={() => setRequire(false)}
            />
            {CONSENT_WORDS.no}
          </label>
        </fieldset>
        <div className="flex flex-wrap gap-2">
          <Button
            pending={agree.isPending}
            disabled={require === null}
            onClick={() => require !== null && agree.mutate({ repositoryId, folder, require })}
          >
            {CONSENT_WORDS.confirmAgree}
          </Button>
          <Button
            variant="secondary"
            pending={withdraw.isPending}
            onClick={() => withdraw.mutate({ repositoryId, folder })}
          >
            {CONSENT_WORDS.withdraw}
          </Button>
        </div>
      </ConfirmPanel>
    </div>
  );
}

export function useReadiness(projectId: string | null) {
  return useQuery<ReadinessCounts>({
    queryKey: ["fix-readiness", projectId],
    enabled: projectId !== null,
    queryFn: ({ signal }) =>
      apiClient.get<ReadinessCounts>(
        `fix-readiness?project_id=${encodeURIComponent(projectId ?? "")}`,
        { signal },
      ),
  });
}

/** FR10b: counts only, with the qualifier; the empty sentence with none. */
export function FixReadinessPanel({ projectId }: { projectId: string | null }) {
  const readiness = useReadiness(projectId);
  if (projectId === null) return null;
  if (readiness.isPending) return <SkeletonRows rows={2} label={CONSENT_WORDS.readinessHeading} />;
  if (readiness.isError) {
    return (
      <InlineFailure
        title={CONSENT_WORDS.readinessHeading}
        error={readiness.error}
        onRetry={() => void readiness.refetch()}
      />
    );
  }
  const counts = readiness.data;
  return (
    <div className="flex flex-col gap-2 text-sm" data-testid="fix-readiness">
      <h3 className="font-medium text-ink">{CONSENT_WORDS.readinessHeading}</h3>
      {counts.findings === 0 ? (
        <p className="max-w-prose text-ink-dim">{counts.sentence}</p>
      ) : (
        <>
          <ul className="flex flex-col gap-1">
            <li>{CONSENT_WORDS.ready(counts.ready)}</li>
            <li>{CONSENT_WORDS.readyWithoutStack(counts.ready_without_stack)}</li>
            <li>{CONSENT_WORDS.elsewhere(counts.cause_is_elsewhere)}</li>
          </ul>
          {counts.services_without_location.length > 0 ? (
            <p className="text-ink-dim [overflow-wrap:anywhere]">
              {CONSENT_WORDS.noLocation}: {counts.services_without_location.join(", ")}
            </p>
          ) : null}
          <p className="max-w-prose text-ink-dim">{counts.sentence}</p>
        </>
      )}
    </div>
  );
}
