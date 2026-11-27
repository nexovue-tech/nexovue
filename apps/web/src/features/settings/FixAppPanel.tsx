import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { apiClient } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { formatTimestamp } from "@/shared/format/time";

import { pollWhileSettingUp, usePublicAllowance } from "./api";

/* F5 Step 3 (plan T3.3, T3.4, T3.6): the Nexovue suggestions App and code locations, on
 * `/settings/connections` (an admin's). Unknown values read as an em dash, never as false. */

export interface FixAppRepository {
  id: string;
  key: string;
  is_private: boolean;
  is_reachable: boolean;
  /** When an admin allowed drafts in this public repository; null while refused (FR42). */
  public_allowed_at: string | null;
}

export interface FixAppView {
  available: boolean;
  state: string | null;
  setup_step: string | null;
  install_url: string | null;
  permissions: Record<string, string> | null;
  granted_at: string | null;
  connection_id: string | null;
  repositories: FixAppRepository[];
}

export interface CodeLocationView {
  id: string;
  service: string;
  project_id: string | null;
  repository_id: string;
  repository: string;
  folder: string | null;
  path_in_production: string | null;
  consented: boolean;
  set_at: string;
}

export interface CodeLocations {
  locations: CodeLocationView[];
  services_without_location: string[];
}

export const FIX_APP_WORDS = {
  heading: "Nexovue suggestions App",
  lead: "Reads the code of the repositories you install it on, so a suggested fix can list files for you to confirm. Nothing is written to any repository.",
  create: "Create the App on GitHub",
  stop: "Stop setting up",
  saveFailed: "The code location was not saved",
  publicRefused: "Public: drafts refused until an admin allows them.",
  publicWarning:
    "A draft's branch in a public repository is public too: anyone can see that something is broken, and where.",
  allowDraftsIn: (key: string) => `Allow drafts in ${key}`,
  publicAllowedSince: (since: string) => `Public. Drafts allowed here since ${since}.`,
  install: "Install the App",
  notCreated: "The Nexovue suggestions App is not set up yet. Create it first.",
  failed: "The Nexovue suggestions App could not be loaded",
  permissions: "Permissions GitHub granted",
  // Required before anyone raises the App to write (design, *Fix App*; foundation, section 6).
  raiseHeading: "Before raising the App to write branches and pull requests",
  raiseWarning:
    "Write access reaches every repository you installed this App on, including branches and tags that may start your deploys. Nexovue only writes to nexovue/suggestion-* branches, and only in repositories an owner has allowed, but GitHub does not enforce that. Protect your main and release branches and tags.",
  raiseSteps:
    "To raise it: on GitHub, open this App's settings, add Contents: read and write and Pull requests: read and write, and save. GitHub's organization owner then accepts the new permissions.",
  signedCommits:
    "If a branch rule requires signed commits, a draft's commit may not count as signed, and that can stop its merge.",
  reachable: "reachable",
  unreachable: "not reachable",
  unknown: "—",
  locationsHeading: "Code locations",
  locationsLead:
    "Where each service's code lives. A blank folder means the whole repository. A folder outside every owner's consent stops suggested fixes for that service until an owner agrees.",
  noServices: "No services seen yet.",
  service: "Service",
  repository: "Repository (owner/name)",
  folder: "Folder",
  production: "Path in production (optional)",
  save: "Save code location",
  consented: "An owner has agreed for this folder",
  notConsented: "No owner has agreed for this folder yet",
  locationsFailed: "Code locations could not be loaded",
} as const;

const FIX_APP_KEY = ["connections", "fix-app"] as const;
const LOCATIONS_KEY = ["code-locations"] as const;

export function useFixApp() {
  return useQuery<FixAppView>({
    queryKey: FIX_APP_KEY,
    queryFn: ({ signal }) => apiClient.get<FixAppView>("connections/github/fix-app", { signal }),
    refetchInterval: (query) => pollWhileSettingUp(query.state.data ? [query.state.data] : []),
  });
}

export function useCodeLocations() {
  return useQuery<CodeLocations>({
    queryKey: LOCATIONS_KEY,
    queryFn: ({ signal }) => apiClient.get<CodeLocations>("code-locations", { signal }),
  });
}

/** The manifest flow: the API answers the manifest; the browser posts it to GitHub. */
function useStartFixApp() {
  return useMutation({
    mutationKey: ["connections", "fix-app", "start"],
    mutationFn: () =>
      apiClient.post<{ state: string; manifest: object; post_url: string }>(
        "connections/github/fix-app/manifest",
        {},
      ),
  });
}

/** Deletes a setup that never came back from GitHub, through the connections route. */
function useStopFixAppSetup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["connections", "fix-app", "stop"],
    mutationFn: (connectionId: string) => apiClient.delete<null>(`connections/${connectionId}`),
    onSettled: () => queryClient.invalidateQueries({ queryKey: FIX_APP_KEY }),
  });
}

function useSetLocation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["code-locations", "set"],
    mutationFn: (row: {
      service: string;
      repository_id: string;
      folder: string;
      path_in_production: string;
    }) => apiClient.put<CodeLocations>("code-locations", { rows: [row] }),
    onSuccess: (answer) => queryClient.setQueryData(LOCATIONS_KEY, answer),
  });
}

/** Posts the manifest to GitHub in the browser, as F22's setup does. */
function postManifest(postUrl: string, state: string, manifest: object): void {
  const form = document.createElement("form");
  form.method = "post";
  form.action = `${postUrl}?state=${encodeURIComponent(state)}`;
  const field = document.createElement("input");
  field.type = "hidden";
  field.name = "manifest";
  field.value = JSON.stringify(manifest);
  form.append(field);
  document.body.append(form);
  form.submit();
}

export function FixAppPanel() {
  const app = useFixApp();
  const start = useStartFixApp();
  const stop = useStopFixAppSetup();
  if (app.isPending) return <SkeletonRows rows={2} label={FIX_APP_WORDS.heading} />;
  if (app.isError) {
    return (
      <InlineFailure
        title={FIX_APP_WORDS.failed}
        error={app.error}
        onRetry={() => void app.refetch()}
      />
    );
  }
  const view = app.data;
  if (!view.available) return null;
  return (
    <section aria-labelledby="fix-app" className="flex flex-col gap-3" data-testid="fix-app">
      <h2 id="fix-app" className="font-medium font-serif text-base text-ink">
        {FIX_APP_WORDS.heading}
      </h2>
      <p className="max-w-prose text-ink-dim text-sm">{FIX_APP_WORDS.lead}</p>
      {/* An expired setup offers a fresh start; the API retires the abandoned one first. */}
      {view.state === null || view.setup_step === "expired" ? (
        <div>
          <Button
            pending={start.isPending}
            onClick={() =>
              start.mutate(undefined, {
                onSuccess: (answer) => postManifest(answer.post_url, answer.state, answer.manifest),
              })
            }
          >
            {FIX_APP_WORDS.create}
          </Button>
        </div>
      ) : (
        <>
          {view.install_url ? (
            <a
              className="text-brand-text text-sm underline-offset-2 hover:underline"
              href={view.install_url}
            >
              {FIX_APP_WORDS.install}
            </a>
          ) : null}
          {view.state === "setting_up" && view.connection_id !== null ? (
            <div>
              {/* A setup left on GitHub is stopped here, as F22's is, rather than waited out. */}
              <Button
                variant="secondary"
                pending={stop.isPending}
                pendingLabel="Stopping…"
                onClick={() => view.connection_id !== null && stop.mutate(view.connection_id)}
              >
                {FIX_APP_WORDS.stop}
              </Button>
            </div>
          ) : null}
          <p className="text-ink text-sm">
            {FIX_APP_WORDS.permissions}:{" "}
            {view.permissions === null
              ? FIX_APP_WORDS.unknown
              : Object.entries(view.permissions)
                  .map(([name, level]) => `${name}: ${level}`)
                  .join(", ")}
          </p>
          <div className="flex flex-col gap-1" data-testid="fix-app-raise">
            <h3 className="font-medium text-ink text-sm">{FIX_APP_WORDS.raiseHeading}</h3>
            <p className="max-w-prose text-ink text-sm">{FIX_APP_WORDS.raiseWarning}</p>
            <p className="max-w-prose text-ink-dim text-sm">{FIX_APP_WORDS.raiseSteps}</p>
            <p className="max-w-prose text-ink-dim text-sm">{FIX_APP_WORDS.signedCommits}</p>
          </div>
          <ul className="flex flex-col gap-1 text-sm">
            {view.repositories.map((repository) => (
              <li key={repository.id} className="flex flex-col gap-1 [overflow-wrap:anywhere]">
                <span>
                  <span className="font-mono">{repository.key}</span> ·{" "}
                  {repository.is_reachable ? FIX_APP_WORDS.reachable : FIX_APP_WORDS.unreachable}
                </span>
                {repository.is_private || view.connection_id === null ? null : (
                  <PublicDrafts connectionId={view.connection_id} repository={repository} />
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      <CodeLocationsPanel app={view} />
    </section>
  );
}

/** FR42: a public repository refuses drafts until an admin allows them, after the warning
 * (live test, 2026-11-28: the panel had no such control, only the issues App had). */
function PublicDrafts({
  connectionId,
  repository,
}: {
  connectionId: string;
  repository: FixAppRepository;
}) {
  const allowance = usePublicAllowance();
  const [confirming, setConfirming] = useState(false);
  const change = (allowed: boolean) =>
    allowance.mutate(
      { connectionId, placeId: repository.id, allowed },
      { onSuccess: () => setConfirming(false) },
    );
  if (repository.public_allowed_at) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-ink-dim">
          {FIX_APP_WORDS.publicAllowedSince(formatTimestamp(repository.public_allowed_at))}
        </span>
        <Button
          variant="secondary"
          pending={allowance.isPending}
          pendingLabel="Withdrawing…"
          onClick={() => change(false)}
        >
          Withdraw
        </Button>
      </div>
    );
  }
  if (confirming) {
    return (
      <ConfirmPanel label={FIX_APP_WORDS.allowDraftsIn(repository.key)}>
        <p className="max-w-prose text-ink text-sm">{FIX_APP_WORDS.publicWarning}</p>
        <div className="mt-3 flex flex-wrap gap-3">
          <Button
            pending={allowance.isPending}
            pendingLabel="Allowing…"
            onClick={() => change(true)}
          >
            {FIX_APP_WORDS.allowDraftsIn(repository.key)}
          </Button>
          <Button variant="secondary" onClick={() => setConfirming(false)}>
            Keep refusing
          </Button>
        </div>
        {allowance.error ? (
          <InlineFailure title="The repository was not changed" error={allowance.error} />
        ) : null}
      </ConfirmPanel>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-ink-dim">{FIX_APP_WORDS.publicRefused}</span>
      <Button variant="secondary" onClick={() => setConfirming(true)}>
        Allow drafts
      </Button>
    </div>
  );
}

export function CodeLocationsPanel({ app }: { app: FixAppView }) {
  const locations = useCodeLocations();
  const set = useSetLocation();
  const [service, setService] = useState("");
  const [repository, setRepository] = useState("");
  const [folder, setFolder] = useState("");
  const [production, setProduction] = useState("");
  if (app.state === null) return <p className="text-ink-dim text-sm">{FIX_APP_WORDS.notCreated}</p>;
  if (locations.isPending) return <SkeletonRows rows={2} label={FIX_APP_WORDS.locationsHeading} />;
  if (locations.isError) {
    return (
      <InlineFailure
        title={FIX_APP_WORDS.locationsFailed}
        error={locations.error}
        onRetry={() => void locations.refetch()}
      />
    );
  }
  const repositoryId = app.repositories.find((each) => each.key === repository.trim())?.id;
  const data = locations.data;
  return (
    <div className="flex flex-col gap-3" data-testid="code-locations">
      <h3 className="font-medium text-ink text-sm">{FIX_APP_WORDS.locationsHeading}</h3>
      <p className="max-w-prose text-ink-dim text-sm">{FIX_APP_WORDS.locationsLead}</p>
      {data.locations.length === 0 && data.services_without_location.length === 0 ? (
        <p className="text-ink-dim text-sm">{FIX_APP_WORDS.noServices}</p>
      ) : null}
      <ul className="flex flex-col gap-1 text-sm">
        {data.locations.map((location) => (
          <li key={location.id} className="[overflow-wrap:anywhere]">
            {location.service} → <span className="font-mono">{location.repository}</span>
            {location.folder ? ` / ${location.folder}` : ""} ·{" "}
            {location.consented ? FIX_APP_WORDS.consented : FIX_APP_WORDS.notConsented}
          </li>
        ))}
        {data.services_without_location.map((name) => (
          <li key={name} className="text-ink-dim">
            {name} · {FIX_APP_WORDS.unknown}
          </li>
        ))}
      </ul>
      <form
        className="flex flex-col gap-2 @3xl:grid @3xl:grid-cols-2 @3xl:gap-x-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (repositoryId === undefined) return;
          set.mutate({
            service: service.trim(),
            repository_id: repositoryId,
            folder: folder.trim(),
            path_in_production: production.trim(),
          });
        }}
      >
        <TextField
          label={FIX_APP_WORDS.service}
          name="service"
          value={service}
          onChange={setService}
        />
        <TextField
          label={FIX_APP_WORDS.repository}
          name="repository"
          value={repository}
          onChange={setRepository}
        />
        {/* Both optional: a blank folder is the whole repository (live test E2). */}
        <TextField
          label={FIX_APP_WORDS.folder}
          name="folder"
          value={folder}
          onChange={setFolder}
          required={false}
        />
        <TextField
          label={FIX_APP_WORDS.production}
          name="production"
          value={production}
          onChange={setProduction}
          required={false}
        />
        <div>
          <Button
            type="submit"
            pending={set.isPending}
            disabled={!service.trim() || repositoryId === undefined}
          >
            {FIX_APP_WORDS.save}
          </Button>
        </div>
        {set.error ? <InlineFailure title={FIX_APP_WORDS.saveFailed} error={set.error} /> : null}
      </form>
    </div>
  );
}
