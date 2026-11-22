import { useState } from "react";

import { ApiError } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { TextField } from "@/shared/components/TextField";
import type { DraftConfirm } from "./api";
import { useOpenDraft } from "./api";
import { DRAFT_WORDS } from "./fix-page";

/**
 * FR24's confirmation (F5 Step 7, T7.8): the repository, branch, file and line counts and the
 * CI mode, every warning by exact string, and the requester's one optional line (FR24a),
 * which a public repository puts in the pull request only when ticked. Every word is the
 * API's, from this person's own passed check; the click carries that check's digest. The
 * diff above is already the check's own, so nobody compares two near-identical diffs.
 */
export function FixDraftConfirm({
  findingId,
  fixId,
  confirm,
  onCancel,
}: {
  findingId: string;
  fixId: string;
  confirm: DraftConfirm;
  onCancel: () => void;
}) {
  const open = useOpenDraft(findingId, fixId);
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const refused = open.error instanceof ApiError ? open.error.problem?.detail : undefined;
  return (
    <ConfirmPanel label={confirm.action}>
      <p className="min-w-0 max-w-prose text-ink [overflow-wrap:anywhere]">{confirm.facts}</p>
      <p className="max-w-prose text-ink">{confirm.ci_mode}</p>
      <ul className="flex max-w-prose list-disc flex-col gap-1 pl-5 text-ink">
        {confirm.warnings.map((warning) => (
          <li key={warning}>{warning}</li>
        ))}
      </ul>
      <TextField label={confirm.note_label} name="requester-note" value={note} onChange={setNote} />
      {confirm.note_public_confirm ? (
        <label className="flex min-h-11 items-center gap-2 text-ink">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          {confirm.note_public_confirm}
        </label>
      ) : null}
      {refused ? (
        <p role="alert" className="max-w-prose text-ink">
          {refused}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-3">
        <Button
          pending={open.isPending}
          onClick={() =>
            open.mutate({
              digest: confirm.digest,
              note: note.trim() ? note.trim() : null,
              note_confirmed: confirmed,
            })
          }
        >
          {confirm.action}
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          {DRAFT_WORDS.cancel}
        </Button>
      </div>
    </ConfirmPanel>
  );
}
