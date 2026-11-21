import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { apiClient } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";

/* F5 Step 3 (plan T3.3, T3.4, T3.6): the Nexovue suggestions App and code locations, on
 * `/settings/connections` (an admin's). Unknown values read as an em dash, never as false. */

export interface FixAppView {
  available: boolean;
  state: string | null;
  setup_step: string | null;
  install_url: string | null;
  permissions: Record<string, string> | null;
  granted_at: string | null;
  connection_id: string | null;
  repositories: { id: string; key: string; is_private: boolean; is_reachable: boolean }[];
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
  install: "Install the App",
  notCreated: "The Nexovue suggestions App is not set up yet. Create it first.",
  failed: "The Nexovue suggestions App could not be loaded",
  permissions: "Permissions GitHub granted",
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
      {view.state === null ? (
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
          <p className="text-ink text-sm">
            {FIX_APP_WORDS.permissions}:{" "}
            {view.permissions === null
              ? FIX_APP_WORDS.unknown
              : Object.entries(view.permissions)
                  .map(([name, level]) => `${name}: ${level}`)
                  .join(", ")}
          </p>
          <ul className="flex flex-col gap-1 text-sm">
            {view.repositories.map((repository) => (
              <li key={repository.id} className="[overflow-wrap:anywhere]">
                <span className="font-mono">{repository.key}</span> ·{" "}
                {repository.is_reachable ? FIX_APP_WORDS.reachable : FIX_APP_WORDS.unreachable}
              </li>
            ))}
          </ul>
        </>
      )}
      <CodeLocationsPanel app={view} />
    </section>
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
        <TextField label={FIX_APP_WORDS.folder} name="folder" value={folder} onChange={setFolder} />
        <TextField
          label={FIX_APP_WORDS.production}
          name="production"
          value={production}
          onChange={setProduction}
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
      </form>
    </div>
  );
}
