import { useState } from "react";

import { ApiError } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import type { DraftView } from "./api";
import { useRunCi } from "./api";
import { DRAFT_WORDS, mergedFactsLine } from "./fix-page";

/**
 * **Run CI** on an open draft, and what followed it (F5 Step 8, T8.3; FR25, FR26, FR30).
 * The API sends the action only while write access is on and no request is waiting; the
 * click first opens FR25's confirmation, by exact string, since it runs the model's code in
 * the customer's CI. A request reads as asked for, then as done, or its failure's sentence
 * (shown with the draft's other failures). After a merge the facts line states the times in
 * order. Every sentence but the facts line's is the API's.
 */
export function FixCi({
  findingId,
  fixId,
  draft,
}: {
  findingId: string;
  fixId: string;
  draft: DraftView;
}) {
  const run = useRunCi(findingId, fixId);
  const [confirming, setConfirming] = useState(false);
  const refused = run.error instanceof ApiError ? run.error.problem?.detail : undefined;
  const facts = mergedFactsLine(draft.merged_facts);
  return (
    <>
      {draft.ci_status ? (
        <p className="max-w-prose text-ink-dim text-sm">{draft.ci_status}</p>
      ) : null}
      {facts.length > 0 ? (
        <ul className="flex max-w-prose flex-col gap-1 text-ink text-sm" data-testid="merged-facts">
          {facts.map((fact) => (
            <li key={fact}>{fact}</li>
          ))}
        </ul>
      ) : null}
      {confirming && draft.ci_confirm && draft.ci_confirm_action ? (
        <ConfirmPanel label={draft.ci_confirm_action}>
          <p className="max-w-prose text-ink">{draft.ci_confirm}</p>
          <div className="flex flex-wrap gap-3">
            <Button
              pending={run.isPending}
              onClick={() => run.mutate(undefined, { onSettled: () => setConfirming(false) })}
            >
              {draft.ci_confirm_action}
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              {DRAFT_WORDS.cancel}
            </Button>
          </div>
        </ConfirmPanel>
      ) : draft.ci_action ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" onClick={() => setConfirming(true)}>
            {draft.ci_action}
          </Button>
        </div>
      ) : null}
      {refused ? (
        <p role="alert" className="max-w-prose text-ink text-sm">
          {refused}
        </p>
      ) : null}
    </>
  );
}
