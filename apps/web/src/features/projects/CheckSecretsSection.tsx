import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { plural } from "@/shared/format/plural";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";

import type { CheckSecretSummary } from "./check-settings-api";
import {
  byName,
  isTaken,
  MAX_SECRETS_PER_PROJECT,
  secretNameProblem,
  secretReference,
  secretValueProblem,
  useCheckSecrets,
  useDeleteCheckSecret,
  useWriteCheckSecret,
} from "./check-settings-api";

/**
 * A project's check secrets: names in, values in, names out.
 *
 * **No value is ever on this screen after it is typed** (FR20, EC12). The list has
 * nothing to show because the API returns nothing, and the replacement field always
 * starts empty — pre-filling it with a placeholder that looks like the old value would
 * suggest the value can be seen, which is the one thing a customer must not believe.
 */
export function CheckSecretsSection({ projectId }: { projectId: string }) {
  const secrets = useCheckSecrets(projectId);

  return (
    <section className="flex flex-col gap-4">
      <div className="max-w-2xl">
        <h3 className="font-medium font-serif text-ink text-base tracking-tight">Check secrets</h3>
        <p className="max-w-3xl mt-1 text-ink-dim text-sm leading-relaxed">
          Tokens, passwords and keys your checks send. Write{" "}
          <code className="font-mono text-ink text-xs">{secretReference("NAME")}</code> in a
          check&rsquo;s URL, a header or the body, or pick the secret in a check&rsquo;s
          Authorization tab. Nobody can read a stored value back — owners included. It can only be
          replaced or deleted, and it is blanked out of everything a check records.
        </p>
      </div>

      {secrets.isPending ? (
        <SkeletonRows rows={3} label="Loading check secrets" />
      ) : secrets.isError ? (
        <InlineFailure
          title="The check secrets could not be loaded"
          error={secrets.error}
          onRetry={() => void secrets.refetch()}
        />
      ) : (
        <>
          {secrets.data.length === 0 ? (
            <EmptyState
              headingLevel={4}
              title="This project has no check secrets"
              detail="Add one below to send a token or password from a check without writing it into the check itself."
            />
          ) : (
            <ul className="max-w-2xl border-line border-t">
              {byName(secrets.data).map((secret) => (
                <SecretRow key={secret.name} projectId={projectId} secret={secret} />
              ))}
            </ul>
          )}
          <AddSecretForm projectId={projectId} existing={secrets.data} />
        </>
      )}
    </section>
  );
}

type RowMode = "reading" | "replacing" | "confirming-delete";

function SecretRow({ projectId, secret }: { projectId: string; secret: CheckSecretSummary }) {
  const remove = useDeleteCheckSecret(projectId);
  const [mode, setMode] = useState<RowMode>("reading");
  const [replaced, setReplaced] = useState(false);
  const reference = secretReference(secret.name);

  return (
    <li className="border-line-subtle border-b py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <div className="min-w-0">
          <p className="font-medium font-mono text-ink text-sm">{secret.name}</p>
          <p className="mt-1 text-ink-dim text-xs">
            <code className="font-mono">{reference}</code> ·{" "}
            <span title={formatTimestamp(secret.updated_at)}>
              updated {formatRelativeTime(secret.updated_at)}
            </span>
          </p>
        </div>
        {mode === "reading" ? (
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                setReplaced(false);
                setMode("replacing");
              }}
            >
              Replace
            </Button>
            <Button
              variant="secondary"
              pending={remove.isPending}
              pendingLabel="Deleting…"
              onClick={() => setMode("confirming-delete")}
            >
              Delete
            </Button>
          </div>
        ) : null}
      </div>

      {replaced ? (
        <p role="status" className="mt-2 text-ink-dim text-sm">
          Replaced. {reference} holds the new value, and the previous one is gone.
        </p>
      ) : null}

      {mode === "replacing" ? (
        <ReplaceSecretForm
          projectId={projectId}
          name={secret.name}
          onCancel={() => setMode("reading")}
          onReplaced={() => {
            setMode("reading");
            setReplaced(true);
          }}
        />
      ) : null}

      {mode === "confirming-delete" ? (
        <div className="mt-3 border-critical-solid border-l-2 bg-critical-subtle px-3 py-3">
          <p className="font-medium text-ink text-sm">Delete {secret.name}?</p>
          <p className="max-w-3xl mt-1 text-ink-dim text-sm leading-relaxed">
            {secret.used_by_checks === 0 ? (
              <>
                No check uses <code className="font-mono text-ink">{reference}</code>.
              </>
            ) : (
              <>
                {secret.used_by_checks} {plural(secret.used_by_checks, "check")}{" "}
                {plural(secret.used_by_checks, "uses", "use")}{" "}
                <code className="font-mono text-ink">{reference}</code> and will become
                misconfigured, sending nothing until a secret named {secret.name} is stored again.
              </>
            )}{" "}
            The value cannot be recovered by anyone, so have it to hand if you will need it.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <Button
              onClick={() => {
                setMode("reading");
                remove.mutate(secret.name);
              }}
            >
              Yes, delete {secret.name}
            </Button>
            <Button variant="secondary" onClick={() => setMode("reading")}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      {remove.error ? (
        <div className="mt-3">
          <InlineFailure title={`${secret.name} was not deleted`} error={remove.error} />
        </div>
      ) : null}
    </li>
  );
}

function ReplaceSecretForm({
  projectId,
  name,
  onCancel,
  onReplaced,
}: {
  projectId: string;
  name: string;
  onCancel: () => void;
  onReplaced: () => void;
}) {
  const write = useWriteCheckSecret(projectId);
  const [value, setValue] = useState("");
  const valueProblem = secretValueProblem(value);

  // The stored value is unknowable, so anything typed is a change.
  const guard = useUnsavedChanges(value !== "");

  return (
    <form
      className="mt-3 flex max-w-md flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (value === "" || valueProblem) return;
        write.mutate({ name, value }, { onSuccess: onReplaced });
      }}
    >
      <UnsavedChangesPrompt guard={guard} what={`a new value for ${name}`} />
      <TextField
        label={`New value for ${name}`}
        name="replacement-value"
        type="password"
        autoComplete="new-password"
        value={value}
        onChange={setValue}
        error={valueProblem}
        hint="The current value is replaced as soon as you save."
      />
      <div className="flex items-center gap-2">
        <Button
          type="submit"
          pending={write.isPending}
          pendingLabel="Replacing…"
          disabled={value === "" || valueProblem !== undefined}
        >
          Replace {name}
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      {write.error ? (
        <InlineFailure title={`${name} was not replaced`} error={write.error} />
      ) : null}
    </form>
  );
}

function AddSecretForm({
  projectId,
  existing,
}: {
  projectId: string;
  existing: readonly CheckSecretSummary[];
}) {
  const write = useWriteCheckSecret(projectId);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [stored, setStored] = useState<string>();

  // `PUT` on a taken name replaces that secret, so adding must never reach it silently.
  const nameProblem = isTaken(name, existing)
    ? `${name} already exists. Use Replace on its row to change the value.`
    : secretNameProblem(name);
  const valueProblem = secretValueProblem(value);
  const atLimit = existing.length >= MAX_SECRETS_PER_PROJECT;
  const ready =
    name !== "" && value !== "" && nameProblem === undefined && valueProblem === undefined;

  const guard = useUnsavedChanges(name !== "" || value !== "");

  return (
    <form
      className="flex max-w-md flex-col gap-3 border border-line bg-surface px-4 py-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!ready || atLimit) return;
        write.mutate(
          { name, value },
          {
            onSuccess: (secret) => {
              write.reset();
              setName("");
              setValue("");
              setStored(secret.name);
            },
          },
        );
      }}
    >
      <UnsavedChangesPrompt guard={guard} what="a new check secret" />
      <p className="font-medium text-ink text-sm">Add a secret</p>
      <TextField
        label="Secret name"
        name="secret-name"
        autoComplete="off"
        value={name}
        onChange={(next) => {
          setStored(undefined);
          setName(next);
        }}
        error={nameProblem}
        hint={name === "" ? "Checks refer to it by this name." : `Used as ${secretReference(name)}`}
      />
      <TextField
        label="Secret value"
        name="secret-value"
        type="password"
        autoComplete="new-password"
        value={value}
        onChange={(next) => {
          setStored(undefined);
          setValue(next);
        }}
        error={valueProblem}
        hint="Shown to nobody once saved, including you."
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="submit"
          pending={write.isPending}
          pendingLabel="Storing…"
          disabled={!ready || atLimit}
        >
          Store secret
        </Button>
        {atLimit ? (
          <span className="text-ink-dim text-xs">
            A project holds at most {MAX_SECRETS_PER_PROJECT} secrets. Delete one to add another.
          </span>
        ) : ready ? null : (
          <span className="text-ink-dim text-xs">A name and a value are required.</span>
        )}
      </div>
      {stored ? (
        <p role="status" className="text-ink text-sm">
          Stored {stored}. Checks can now use{" "}
          <code className="font-mono">{secretReference(stored)}</code>; its value will not be shown
          again.
        </p>
      ) : null}
      {write.error ? <InlineFailure title="The secret was not stored" error={write.error} /> : null}
    </form>
  );
}
