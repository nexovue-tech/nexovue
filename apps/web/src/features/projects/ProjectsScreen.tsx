import { ChevronRight } from "lucide-react";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";

import type { ProjectDeleted } from "./api";
import { useCreateProject, useProject, useProjects } from "./api";
import { ProjectSettings } from "./ProjectSettings";

/**
 * P1-76. The projects an organization has, and everything that is true of one.
 *
 * A project is the unit a key belongs to and telemetry is filed under, so this
 * screen is also where two things that look unrelated are decided together:
 * what a project keeps (sampling) and what never leaves the customer's process
 * at all (masking). They sit here rather than on their own screens because both
 * answers are per project and neither means anything without one.
 *
 * The list and the detail are separate reads. The summary carries a name and a
 * slug; the two config documents come with the detail, and fetching every one of
 * them to draw a list would be a page of documents nobody asked to see.
 */
export function ProjectsScreen() {
  const projects = useProjects();
  const [selectedId, setSelectedId] = useState<string>();
  const [deleted, setDeleted] = useState<ProjectDeleted>();

  const selected = useProject(selectedId);

  const onDeleted = (result: ProjectDeleted) => {
    setDeleted(result);
    setSelectedId(undefined);
  };

  return (
    <div className="flex flex-col gap-8 px-6 py-8">
      <header className="max-w-2xl">
        <h1 className="font-medium font-serif text-ink text-xl tracking-tight">Projects</h1>
        <p className="mt-2 text-ink-dim text-sm leading-relaxed">
          A project is what a key sends to and what telemetry is filed under. Most teams keep one
          per environment &mdash; production and staging &mdash; so a staging deploy can never be
          mistaken for a production incident.
        </p>
      </header>

      {deleted ? (
        <DeletionReceipt result={deleted} onDismiss={() => setDeleted(undefined)} />
      ) : null}

      <CreateProject />

      {projects.isPending ? (
        <SkeletonRows rows={3} label="Loading projects" />
      ) : projects.isError ? (
        <InlineFailure
          title="Projects could not be loaded"
          error={projects.error}
          onRetry={() => void projects.refetch()}
        />
      ) : projects.data.length === 0 ? (
        <EmptyState
          title="This organization has no projects"
          detail="Nothing can send telemetry until one exists, because a key belongs to a project. Create one above."
        />
      ) : (
        <section>
          {/* The list said nothing about being a list: no count, no heading, and a
              row whose only affordance was a hover class that does not exist. A
              reader could not tell there was anything to click. */}
          <h2 className="border-line border-b px-4 pb-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps">
            {projects.data.length} {projects.data.length === 1 ? "project" : "projects"} — open one
            to change what it keeps
          </h2>
          <ul className="flex flex-col">
            {projects.data.map((project) => (
              <li key={project.id} className="border-line-subtle border-b">
                <button
                  type="button"
                  className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-hover"
                  aria-expanded={selectedId === project.id}
                  onClick={() => setSelectedId(selectedId === project.id ? undefined : project.id)}
                >
                  <ChevronRight
                    size={14}
                    aria-hidden="true"
                    className={`shrink-0 text-ink-dim transition-transform ${
                      selectedId === project.id ? "rotate-90" : ""
                    }`}
                  />
                  <span className="text-ink text-sm">{project.name}</span>
                  <span className="font-mono text-ink-dim text-xs">{project.slug}</span>
                </button>

                {selectedId === project.id ? (
                  <div className="px-4 pb-8">
                    {selected.isPending ? (
                      <SkeletonRows rows={4} label="Loading project settings" />
                    ) : selected.isError ? (
                      <InlineFailure
                        title="This project's settings could not be loaded"
                        error={selected.error}
                        onRetry={() => void selected.refetch()}
                      />
                    ) : (
                      <ProjectSettings
                        key={selected.data.id}
                        project={selected.data}
                        onDeleted={onDeleted}
                      />
                    )}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function CreateProject() {
  const create = useCreateProject();
  const [name, setName] = useState("");

  return (
    <form
      className="flex max-w-md flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate({ name }, { onSuccess: () => setName("") });
      }}
    >
      <TextField
        label="New project"
        name="name"
        value={name}
        onChange={setName}
        // The slug is derived from the name by the API. Asking for both here
        // would be two fields where one is almost always the other lowercased.
        hint="The slug is derived from this and can be changed afterwards."
      />
      <div>
        <Button
          type="submit"
          pending={create.isPending}
          pendingLabel="Creating"
          disabled={name.trim() === ""}
        >
          Create project
        </Button>
      </div>
      {create.error ? (
        <InlineFailure title="The project could not be created" error={create.error} />
      ) : null}
    </form>
  );
}

/**
 * What deletion actually did, kept on screen until dismissed.
 *
 * The endpoint answers 200 with a body rather than 204 precisely so this can be
 * said: every count is a credential that stopped working, and a customer who
 * deletes a project and is told nothing finds out when a deploy goes quiet.
 */
function DeletionReceipt({ result, onDismiss }: { result: ProjectDeleted; onDismiss: () => void }) {
  return (
    <div className="max-w-2xl rounded border border-line bg-raised p-4 text-sm">
      <p className="text-ink">The project was deleted.</p>
      <ul className="mt-2 flex flex-col gap-1 text-ink-dim">
        <li>{result.api_keys_revoked} ingest keys revoked</li>
        <li>{result.site_keys_revoked} browser keys revoked</li>
        <li>{result.webhook_secrets_revoked} webhook secrets revoked</li>
      </ul>
      <p className="mt-2 text-ink-dim">{result.telemetry_notice}</p>
      <div className="mt-3">
        <Button variant="secondary" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </div>
  );
}
