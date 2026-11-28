import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { TextField } from "@/shared/components/TextField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { useCan } from "@/shared/identity";

import {
  type RunbookFields,
  type RunbookView,
  useChatStanding,
  useCreateRunbook,
  useUpdateRunbook,
} from "./api";
import {
  asksForSecondClick,
  byteLength,
  localRefusal,
  refusalSentence,
  SAVE,
  SAVE_ANYWAY,
  SIZE_SHOWN_FROM,
  sentTo,
  sizeLine,
} from "./runbook-words";

interface Draft {
  title: string;
  service: string;
  content: string;
}

const EMPTY_DRAFT: Draft = { title: "", service: "", content: "" };

function fields(draft: Draft, confirmed: boolean): RunbookFields {
  return {
    title: draft.title,
    service_name: draft.service.trim() === "" ? null : draft.service,
    content: draft.content,
    confirm_credentials: confirmed,
  };
}

/**
 * Add or change a runbook (design, `/settings/runbooks`): a plain full-width text box, the
 * sentences around it capped. With chat on it says who the text may go to and not to paste
 * secrets; text that looks like a credential needs a second click (FR32, AC69). Leaving with
 * changes warns.
 */
export function RunbookForm({
  projectId,
  runbook,
  onDone,
}: {
  projectId: string;
  runbook?: RunbookView;
  onDone: () => void;
}) {
  const initial: Draft = runbook
    ? { title: runbook.title, service: runbook.service_name ?? "", content: runbook.content ?? "" }
    : EMPTY_DRAFT;
  const [draft, setDraft] = useState<Draft>(initial);
  const [problem, setProblem] = useState<string | null>(null);
  const create = useCreateRunbook(projectId);
  const update = useUpdateRunbook(runbook?.id ?? "");
  const save = runbook ? update : create;
  const standing = useChatStanding(projectId, useCan("ask_assistant"));
  const dirty =
    draft.title !== initial.title ||
    draft.service !== initial.service ||
    draft.content !== initial.content;
  const guard = useUnsavedChanges(dirty && !save.isSuccess);
  const bytes = byteLength(draft.content);
  const secondClick = save.isError && asksForSecondClick(save.error);
  const provider = standing.data?.state === "ready" ? (standing.data.provider_label ?? null) : null;

  const submit = (confirmed: boolean) => {
    const refusal = localRefusal(draft.title, draft.content);
    setProblem(refusal);
    if (refusal) return;
    save.mutate(fields(draft, confirmed), { onSuccess: onDone });
  };

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        submit(false);
      }}
    >
      <TextField
        label="Title"
        name="title"
        value={draft.title}
        onChange={(title) => setDraft({ ...draft, title })}
      />
      <TextField
        label="Service (optional)"
        name="service"
        required={false}
        value={draft.service}
        onChange={(service) => setDraft({ ...draft, service })}
      />
      <label className="flex flex-col gap-1.5">
        <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">What to do</span>
        <textarea
          rows={14}
          className="w-full rounded border border-line bg-transparent px-2 py-1.5 font-mono text-ink text-sm"
          value={draft.content}
          onChange={(event) => setDraft({ ...draft, content: event.target.value })}
        />
      </label>
      {bytes >= SIZE_SHOWN_FROM ? <p className="text-ink-dim text-xs">{sizeLine(bytes)}</p> : null}
      {provider ? <p className="max-w-prose text-ink-dim text-xs">{sentTo(provider)}</p> : null}
      {problem ? <p className="max-w-prose text-critical-text text-sm">{problem}</p> : null}
      {save.isError ? (
        <p className="max-w-prose text-critical-text text-sm">{refusalSentence(save.error)}</p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {secondClick ? (
          <Button pending={save.isPending} onClick={() => submit(true)}>
            {SAVE_ANYWAY}
          </Button>
        ) : (
          <Button type="submit" pending={save.isPending}>
            {SAVE}
          </Button>
        )}
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
      </div>
      <UnsavedChangesPrompt guard={guard} what="this runbook" />
    </form>
  );
}
