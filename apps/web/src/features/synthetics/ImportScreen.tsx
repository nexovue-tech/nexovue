import { useNavigate } from "@tanstack/react-router";
import { useEffect, useId, useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { Permitted } from "@/shared/components/Permitted";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { Tabs } from "@/shared/components/Tabs";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { useNothingLive } from "@/shared/refresh";
import { useSelection } from "@/shared/selection";
import { showToast } from "@/shared/toast/toast-store";
import { ImportPreview } from "./ImportPreview";
import type { ImportPlan, ImportPreviewInput, ImportSource, PostmanFolderMode } from "./import-api";
import { IMPORT_SOURCES, useConfirmImport, usePreviewImport } from "./import-api";
import type { ImportDraft } from "./import-plan";
import {
  buildConfirmBody,
  draftFromPlan,
  hasProblems,
  placeConfirmRefusal,
  SOURCE_NAMES,
  successMessage,
  validateDraft,
} from "./import-plan";
import { LazyCodeEditor } from "./LazyCodeEditor";

/**
 * Synthetics → Import: a cURL command, an OpenAPI description or a Postman collection,
 * previewed, completed, then created (FR11–FR15).
 *
 * The server reads every format; this screen only chooses what to send and what to
 * keep. Nothing exists until Create, so a preview with nothing created is unsaved work
 * and is guarded like any other form.
 */

/** `checkimports.schemas.MAX_IMPORT_CHARACTERS`, stated before a file is sent. */
const MAX_IMPORT_BYTES = 5 * 1024 * 1024;
const MAX_IMPORT_MEGABYTES = MAX_IMPORT_BYTES / (1024 * 1024);
const OPENAPI_ACCEPT = ".json,.yaml,.yml";
const POSTMAN_ACCEPT = ".json";

const FIELD_LABEL = "font-mono text-2xs text-ink-dim uppercase tracking-caps";

const MISSING_SOURCE: Record<ImportSource, string> = {
  curl: "Paste a cURL command to preview.",
  openapi: "Choose an OpenAPI file, or paste its contents, to preview.",
  postman: "Choose a Postman collection file to preview.",
};

interface LoadedFile {
  name: string;
  text: string;
}

export function ImportScreen() {
  return (
    <Permitted action="operate_checks" who="an owner, an admin or a member">
      <ImportScreenForm />
    </Permitted>
  );
}

function ImportScreenForm() {
  // Nothing here refreshes on a timer; see NewCheckScreen.
  useNothingLive();
  const { projectId } = useSelection();
  const navigate = useNavigate();
  const preview = usePreviewImport(projectId);
  const confirm = useConfirmImport(projectId);

  const [source, setSource] = useState<ImportSource>("curl");
  const [curlText, setCurlText] = useState("");
  const [openapiText, setOpenapiText] = useState("");
  const [openapiFile, setOpenapiFile] = useState<string>();
  const [collection, setCollection] = useState<LoadedFile>();
  const [environment, setEnvironment] = useState<LoadedFile>();
  const [folders, setFolders] = useState<PostmanFolderMode>("separate");
  const [sourceProblem, setSourceProblem] = useState<string>();

  const [plan, setPlan] = useState<ImportPlan>();
  const [draft, setDraft] = useState<ImportDraft>();
  const [previewed, setPreviewed] = useState<ImportPreviewInput>();
  const [attempted, setAttempted] = useState(false);
  const [sentKeys, setSentKeys] = useState<string[]>([]);
  const [created, setCreated] = useState(false);

  const guard = useUnsavedChanges(plan !== undefined && !created);

  // Navigates after the render that cleared the guard, so leaving is not held by it.
  useEffect(() => {
    if (created) void navigate({ to: "/synthetics" });
  }, [created, navigate]);

  if (!projectId) {
    return (
      <EmptyState
        title="Choose a project first"
        detail="Imported checks belong to a project. Pick one from the project menu, then come back here."
      />
    );
  }

  function inputFor(chosen: ImportSource): ImportPreviewInput | undefined {
    if (chosen === "curl") {
      return curlText.trim() ? { source: "curl", content: curlText } : undefined;
    }
    if (chosen === "openapi") {
      return openapiText.trim() ? { source: "openapi", content: openapiText } : undefined;
    }
    return collection
      ? {
          source: "postman",
          content: collection.text,
          environment: environment?.text ?? null,
          postman_folders: folders,
        }
      : undefined;
  }

  function runPreview(input: ImportPreviewInput, carryOver: boolean) {
    preview.mutate(input, {
      onSuccess: (next) => {
        setPlan(next);
        setDraft((previous) => draftFromPlan(next, carryOver ? previous : undefined));
        setPreviewed(input);
        setAttempted(false);
        setSentKeys([]);
        confirm.reset();
      },
    });
  }

  function requestPreview() {
    const input = inputFor(source);
    if (!input) {
      setSourceProblem(MISSING_SOURCE[source]);
      return;
    }
    setSourceProblem(undefined);
    runPreview(input, false);
  }

  function chooseServer(serverUrl: string) {
    if (previewed) runPreview({ ...previewed, server_url: serverUrl }, true);
  }

  function create() {
    if (!plan || !draft) return;
    setAttempted(true);
    if (hasProblems(validateDraft(plan, draft))) return;
    const { body, keys } = buildConfirmBody(plan, draft);
    setSentKeys(keys);
    confirm.mutate(body, {
      onSuccess: (result) => {
        showToast(successMessage(plan, draft, result.created.length));
        setCreated(true);
      },
    });
  }

  function readFile(file: File | undefined, keep: (loaded: LoadedFile) => void) {
    if (!file) return;
    if (file.size > MAX_IMPORT_BYTES) {
      setSourceProblem(
        `${file.name} is larger than ${MAX_IMPORT_MEGABYTES} MB, the most an import reads.`,
      );
      return;
    }
    file.text().then(
      (text) => {
        setSourceProblem(undefined);
        keep({ name: file.name, text });
      },
      () => setSourceProblem(`${file.name} could not be read.`),
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <UnsavedChangesPrompt guard={guard} what="an import preview that has not been created" />

      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Import checks</h1>
        <p className="mt-1 max-w-2xl text-ink-dim text-sm">
          Paste a cURL command, or choose an OpenAPI or Postman file. You will see every check it
          would create, the credentials that become secrets and anything skipped — nothing is
          created until you say so.
        </p>
      </header>

      <div className="flex w-full min-w-0 max-w-4xl flex-col gap-8 px-4 pb-10">
        <form
          noValidate
          className="flex min-w-0 flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            requestPreview();
          }}
        >
          <Tabs
            label="Import from"
            items={IMPORT_SOURCES.map((id) => ({ id, label: SOURCE_NAMES[id] }))}
            selected={source}
            onSelect={(id) => {
              setSource(IMPORT_SOURCES.find((known) => known === id) ?? "curl");
              setSourceProblem(undefined);
            }}
          >
            <div className="flex flex-col gap-4 pt-4">
              {source === "curl" ? (
                <CurlSource value={curlText} onChange={setCurlText} />
              ) : source === "openapi" ? (
                <>
                  <FilePicker
                    label="OpenAPI file"
                    accept={OPENAPI_ACCEPT}
                    chosen={openapiFile}
                    hint="Swagger 2.0 or OpenAPI 3.0, 3.1 or 3.2, as JSON or YAML, up to 5 MB. It must be self-contained: references to other files or URLs are refused."
                    onFile={(file) =>
                      readFile(file, (loaded) => {
                        setOpenapiFile(loaded.name);
                        setOpenapiText(loaded.text);
                      })
                    }
                  />
                  <LazyCodeEditor
                    label="Or paste the description"
                    language="text"
                    value={openapiText}
                    onChange={setOpenapiText}
                    minLines={6}
                    maxLines={16}
                  />
                </>
              ) : (
                <>
                  <FilePicker
                    label="Postman collection"
                    accept={POSTMAN_ACCEPT}
                    chosen={collection?.name}
                    hint="A collection exported as v2.1. Scripts in it are listed as skipped and never run."
                    onFile={(file) => readFile(file, setCollection)}
                  />
                  <FilePicker
                    label="Environment (optional)"
                    accept={POSTMAN_ACCEPT}
                    chosen={environment?.name}
                    hint="Its values fill the collection's variables; values it marks secret become secrets."
                    onFile={(file) => readFile(file, setEnvironment)}
                  />
                  <FolderChoice value={folders} onChange={setFolders} />
                </>
              )}
            </div>
          </Tabs>

          {sourceProblem ? (
            <p role="alert" className="text-critical-text text-sm">
              {sourceProblem}
            </p>
          ) : null}
          {preview.error ? (
            <InlineFailure title="This could not be imported" error={preview.error} />
          ) : null}

          <div>
            <Button
              type="submit"
              variant={plan ? "secondary" : "primary"}
              pending={preview.isPending}
              pendingLabel="Reading"
            >
              Preview
            </Button>
          </div>
        </form>

        {preview.isPending ? (
          <SkeletonRows rows={6} label="Reading the import" />
        ) : plan && draft ? (
          <ImportPreview
            plan={plan}
            draft={draft}
            onDraftChange={setDraft}
            problems={attempted ? validateDraft(plan, draft) : undefined}
            refusal={placeConfirmRefusal(confirm.error, sentKeys)}
            createError={confirm.error}
            creating={confirm.isPending}
            onCreate={create}
            serverUrl={previewed?.server_url ?? undefined}
            onServerChange={chooseServer}
          />
        ) : null}
      </div>
    </div>
  );
}

function CurlSource({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className={FIELD_LABEL}>
        cURL command
      </label>
      <textarea
        id={id}
        name="curl"
        value={value}
        rows={6}
        spellCheck={false}
        aria-describedby={`${id}-hint`}
        onChange={(event) => onChange(event.target.value)}
        className="w-full min-w-0 rounded-sm border border-line bg-surface px-2.5 py-1.5 font-mono text-ink text-xs outline-none hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
      />
      <p id={`${id}-hint`} className="text-ink-dim text-xs">
        The bash form, from your API's docs or your browser's Copy as cURL. Credentials in it become
        secrets.
      </p>
    </div>
  );
}

function FilePicker({
  label,
  accept,
  chosen,
  hint,
  onFile,
}: {
  label: string;
  accept: string;
  chosen: string | undefined;
  hint: string;
  onFile: (file: File | undefined) => void;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className={FIELD_LABEL}>
        {label}
      </label>
      <input
        id={id}
        type="file"
        accept={accept}
        aria-describedby={`${id}-hint`}
        onChange={(event) => onFile(event.target.files?.[0])}
        className="max-w-full text-ink text-sm file:mr-3 file:rounded-sm file:border file:border-line file:bg-surface file:px-2.5 file:py-1 file:text-ink file:text-sm"
      />
      <p id={`${id}-hint`} className="text-ink-dim text-xs">
        {chosen ? <span className="font-mono text-ink">{chosen} · </span> : null}
        {hint}
      </p>
    </div>
  );
}

function FolderChoice({
  value,
  onChange,
}: {
  value: PostmanFolderMode;
  onChange: (mode: PostmanFolderMode) => void;
}) {
  const id = useId();
  return (
    <div className="flex max-w-md flex-col gap-1.5">
      <label htmlFor={id} className={FIELD_LABEL}>
        Folders become
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value as PostmanFolderMode)}
        className="w-full rounded-sm border border-line bg-surface px-2 py-1.5 text-ink text-sm hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
      >
        <option value="separate">separate checks, one per request</option>
        <option value="multistep">one multistep check each, requests in order</option>
      </select>
    </div>
  );
}
