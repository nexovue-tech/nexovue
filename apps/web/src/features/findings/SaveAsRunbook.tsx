import { Link } from "@tanstack/react-router";

import { ApiError, problemCode } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";

import { useSaveNoteAsRunbook } from "./api";
import {
  NOT_SAVED_AS_RUNBOOK,
  OPEN_SAVED_RUNBOOK,
  SAVE_ANYWAY,
  SAVE_AS_RUNBOOK,
  SAVED_AS_RUNBOOK,
} from "./finding-words";

/** The runbook service's code for text that looks like a credential (F4 FR32, AC69). */
const CREDENTIAL_CODE = "looks_like_credential";

/**
 * **Save as runbook** on a resolved case's note (F4 FR41): one click copies the note and its
 * case's fact line into a runbook that outlives the finding. A note that looks like it holds
 * a credential is refused in the API's own sentence, with a second click to save anyway, as a
 * runbook's own form asks.
 */
export function SaveAsRunbook({ noteId }: { noteId: string }) {
  const save = useSaveNoteAsRunbook();
  const asksAgain = problemCode(save.error) === CREDENTIAL_CODE;

  if (save.data) {
    return (
      <p className="text-ink-dim text-xs" role="status">
        {SAVED_AS_RUNBOOK}{" "}
        <Link
          to="/settings/runbooks/$runbookId"
          params={{ runbookId: save.data.id }}
          className="font-medium text-brand-text underline-offset-2 hover:underline"
        >
          {OPEN_SAVED_RUNBOOK}
        </Link>
      </p>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Button
        variant="secondary"
        pending={save.isPending}
        pendingLabel="Saving"
        onClick={() => save.mutate({ noteId, confirmCredentials: asksAgain })}
      >
        {asksAgain ? SAVE_ANYWAY : SAVE_AS_RUNBOOK}
      </Button>
      {save.error ? (
        <span role="alert" className="max-w-prose text-warning-text text-xs">
          {(save.error instanceof ApiError && save.error.problem?.detail) || NOT_SAVED_AS_RUNBOOK}
        </span>
      ) : null}
    </span>
  );
}
