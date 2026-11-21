import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { useProjects } from "@/shared/projects";

import { type RefusedFile, type UploadedFile, type UploadResult, useUploadRunbooks } from "./api";
import {
  CHOOSE_PROJECTS,
  CREDENTIAL_CODE,
  NO_FILES,
  NO_PROJECT,
  refusalSentence,
  UPLOAD,
  UPLOAD_ANYWAY,
  uploadedLine,
} from "./runbook-words";

/**
 * Several Markdown files at once, into one or more projects as separate copies, in one
 * action, each file refused or saved on its own (AC92). A file refused for looking like it
 * holds a credential can be sent again with the second click.
 */
export function UploadForm({ projectId, onDone }: { projectId: string; onDone: () => void }) {
  const projects = useProjects();
  const upload = useUploadRunbooks();
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [chosen, setChosen] = useState<string[]>([projectId]);
  const [problem, setProblem] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);

  const run = async (batches: Batch[], confirmed: boolean) => {
    const merged: UploadResult = { created: [], refused: [] };
    try {
      for (const batch of batches) {
        const answer = await upload.mutateAsync({
          project_ids: batch.projectIds,
          files: batch.files,
          confirm_credentials: confirmed,
        });
        merged.created.push(...answer.created);
        merged.refused.push(...answer.refused);
      }
    } finally {
      setResult(merged);
    }
  };
  const send = () => {
    if (files.length === 0) return setProblem(NO_FILES);
    if (chosen.length === 0) return setProblem(NO_PROJECT);
    setProblem(null);
    void run([{ projectIds: chosen, files }], false).catch(() => undefined);
  };
  const credentialRefusals = (result?.refused ?? []).filter(
    (refused) => refused.code === CREDENTIAL_CODE,
  );

  return (
    <div className="flex flex-col gap-3">
      <input
        type="file"
        multiple
        accept=".md,.markdown,.txt,text/markdown,text/plain"
        aria-label="Markdown files"
        onChange={async (event) => {
          const picked = [...(event.target.files ?? [])];
          setFiles(
            await Promise.all(
              picked.map(async (file) => ({ name: file.name, content: await file.text() })),
            ),
          );
          setResult(null);
        }}
      />
      <fieldset className="flex flex-col gap-1">
        <legend className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
          {CHOOSE_PROJECTS}
        </legend>
        {(projects.data ?? []).map((project) => (
          <label key={project.id} className="flex items-center gap-2 text-ink text-sm">
            <input
              type="checkbox"
              checked={chosen.includes(project.id)}
              onChange={(event) =>
                setChosen(
                  event.target.checked
                    ? [...chosen, project.id]
                    : chosen.filter((id) => id !== project.id),
                )
              }
            />
            {project.name}
          </label>
        ))}
      </fieldset>
      {problem ? <p className="max-w-prose text-critical-text text-sm">{problem}</p> : null}
      {upload.isError ? (
        <p className="max-w-prose text-critical-text text-sm">{refusalSentence(upload.error)}</p>
      ) : null}
      {result ? <Outcome result={result} names={projectNames(projects.data)} /> : null}
      <div className="flex flex-wrap gap-2">
        <Button pending={upload.isPending} onClick={send}>
          {UPLOAD}
        </Button>
        {credentialRefusals.length > 0 ? (
          <Button
            variant="secondary"
            pending={upload.isPending}
            onClick={() =>
              void run(credentialBatches(credentialRefusals, files), true).catch(() => undefined)
            }
          >
            {UPLOAD_ANYWAY}
          </Button>
        ) : null}
        <Button variant="secondary" onClick={onDone}>
          Close
        </Button>
      </div>
    </div>
  );
}

function projectNames(projects: { id: string; name: string }[] | undefined): Map<string, string> {
  return new Map((projects ?? []).map((project) => [project.id, project.name]));
}

interface Batch {
  projectIds: string[];
  files: UploadedFile[];
}

/** Only the files refused for a credential, to only the projects that refused them. */
export function credentialBatches(refused: RefusedFile[], files: UploadedFile[]): Batch[] {
  return [...new Set(refused.map((each) => each.project_id))].map((projectId) => {
    const names = new Set(
      refused.filter((each) => each.project_id === projectId).map((each) => each.name),
    );
    return { projectIds: [projectId], files: files.filter((file) => names.has(file.name)) };
  });
}

function Outcome({ result, names }: { result: UploadResult; names: Map<string, string> }) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <p className="text-ink">{uploadedLine(result.created.length)}</p>
      {result.refused.length > 0 ? (
        <ul aria-label="Files not added" className="flex flex-col gap-1">
          {result.refused.map((refused) => (
            <li
              key={`${refused.project_id}|${refused.name}`}
              className="max-w-prose text-critical-text"
            >
              <span className="break-words font-mono">{refused.name}</span>
              {names.size > 1 ? ` (${names.get(refused.project_id) ?? refused.project_id})` : ""}:{" "}
              {refused.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
