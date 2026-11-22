import type { ReactNode } from "react";
import { useState } from "react";

import { ApiError } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import type { DraftView } from "./api";
import { useCheckForDraft, useDeleteBranch } from "./api";
import { FixDraftConfirm } from "./FixDraftConfirm";
import { DRAFT_WORDS } from "./fix-page";

const LINK = "font-medium text-brand-text text-sm underline-offset-2 hover:underline";

/**
 * The draft pull request (F5 Step 7, T7.8; FR10c, FR22 to FR26). **Write access is off in
 * code until the founder's go** (ADR-097): the API then sends no action and the
 * `writes_off` sentence in its place, which is all this shows. With the go: **Open draft
 * pull request** asks this person's own `draft` check; the section polls it; once it passes,
 * FR24's confirm opens. A refused draft is hidden with its sentence beside the patch action,
 * never a disabled button (FR10c). An opened draft shows its state, its link and FR26's
 * line. Every sentence is the API's.
 */
export function FixDraft({
  findingId,
  fixId,
  draft,
  linkStep,
}: {
  findingId: string;
  fixId: string;
  draft: DraftView;
  /** The GitHub account link step, when this person has none yet (D6). */
  linkStep: (sentence: string | null) => ReactNode;
}) {
  const check = useCheckForDraft(findingId, fixId);
  const remove = useDeleteBranch(findingId, fixId);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [linking, setLinking] = useState(false);
  const confirm = draft.confirm && draft.confirm.digest !== dismissed ? draft.confirm : null;
  const refused = errorDetail(check.error) ?? errorDetail(remove.error);
  return (
    <div className="flex min-w-0 flex-col gap-2" data-testid="fix-draft">
      {draft.failure ? (
        <p className="max-w-prose text-ink text-sm [overflow-wrap:anywhere]">{draft.failure}</p>
      ) : null}
      {draft.refusal ? <p className="max-w-prose text-ink-dim text-sm">{draft.refusal}</p> : null}
      {draft.pull_request_sentence ? (
        <p className="max-w-prose text-ink text-sm [overflow-wrap:anywhere]">
          {draft.pull_request_url ? (
            <a className={LINK} href={draft.pull_request_url} rel="noreferrer" target="_blank">
              {draft.pull_request_sentence}
            </a>
          ) : (
            draft.pull_request_sentence
          )}
        </p>
      ) : null}
      {draft.ci_note ? <p className="max-w-prose text-ink-dim text-sm">{draft.ci_note}</p> : null}
      {draft.check ? (
        <>
          <h4 className="font-medium text-ink text-sm">{draft.check.heading}</h4>
          {draft.check.sentence ? (
            <p className="max-w-prose text-ink text-sm">{draft.check.sentence}</p>
          ) : null}
        </>
      ) : null}
      {confirm ? (
        <FixDraftConfirm
          findingId={findingId}
          fixId={fixId}
          confirm={confirm}
          onCancel={() => setDismissed(confirm.digest)}
        />
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        {draft.action && !confirm ? (
          <Button
            variant="secondary"
            pending={check.isPending}
            onClick={() => (draft.needs_link ? setLinking(true) : check.mutate())}
          >
            {draft.action}
          </Button>
        ) : null}
        {draft.delete_branch_action ? (
          <Button variant="secondary" pending={remove.isPending} onClick={() => remove.mutate()}>
            {draft.delete_branch_action}
          </Button>
        ) : null}
      </div>
      {linking && draft.needs_link ? linkStep(draft.link_sentence) : null}
      {refused ? (
        <p role="alert" className="max-w-prose text-ink text-sm">
          {refused}
        </p>
      ) : null}
      {remove.isSuccess ? (
        <p role="status" className="text-ink-dim text-sm">
          {DRAFT_WORDS.deleteAsked}
        </p>
      ) : null}
    </div>
  );
}

/** The API's own sentence for a refusal, shown as it says it. */
function errorDetail(error: unknown): string | undefined {
  return error instanceof ApiError ? error.problem?.detail : undefined;
}
