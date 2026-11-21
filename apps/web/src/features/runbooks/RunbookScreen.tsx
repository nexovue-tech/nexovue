import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { useCan } from "@/shared/identity";
import { writtenAge } from "@/shared/runbook-age";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import { useDeleteRunbook, useRunbook } from "./api";
import { RunbookForm } from "./RunbookForm";
import {
  DELETE,
  DELETE_CONFIRM,
  EDIT,
  FORMER_MEMBER,
  refusalSentence,
  SUPPORT_NO_BODY,
  TITLE,
} from "./runbook-words";

/**
 * `/settings/runbooks/$runbookId` (F4 T5.3): one runbook, read by anyone signed in, and by
 * a citation in a chat answer. A support view sees no body (FR32). Who may write edits it in
 * place, or deletes it behind a confirmation saying what chat loses.
 */
export function RunbookScreen({ runbookId }: { runbookId: string }) {
  const runbook = useRunbook(runbookId);
  const may = useCan("manage_knowledge");
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const remove = useDeleteRunbook(runbookId);
  const navigate = useNavigate();
  const now = Date.now();
  const back = (
    <Link
      to="/settings/runbooks"
      className="text-brand-text text-sm underline-offset-2 hover:underline"
    >
      All runbooks
    </Link>
  );
  if (runbook.isPending) {
    return (
      <SettingsLayout title={TITLE}>
        <SkeletonRows rows={6} label="Loading the runbook" />
      </SettingsLayout>
    );
  }
  if (runbook.isError) {
    return (
      <SettingsLayout title={TITLE}>
        {back}
        <InlineFailure
          title="The runbook could not be loaded"
          error={runbook.error}
          onRetry={() => void runbook.refetch()}
        />
      </SettingsLayout>
    );
  }
  const found = runbook.data;
  const age = writtenAge(found.updated_at, now);
  return (
    <SettingsLayout title={found.title}>
      <div className="flex flex-col gap-3">
        {back}
        <p className="text-ink-dim text-xs">
          {found.service_name ? (
            <span className="break-words font-mono">{found.service_name} · </span>
          ) : null}
          {found.author ?? FORMER_MEMBER} ·{" "}
          <span className={age.stale ? "text-warning-text" : undefined}>{age.words}</span>
        </p>
        {editing ? (
          <RunbookForm
            projectId={found.project_id}
            runbook={found}
            onDone={() => setEditing(false)}
          />
        ) : found.content === null ? (
          <p className="max-w-prose text-ink-dim text-sm">{SUPPORT_NO_BODY}</p>
        ) : (
          <pre className="whitespace-pre-wrap break-words rounded-sm border border-line p-3 font-mono text-ink text-sm">
            {found.content}
          </pre>
        )}
        {may && !editing && found.content !== null ? (
          confirming ? (
            <ConfirmPanel label={DELETE}>
              <p className="max-w-prose">{DELETE_CONFIRM}</p>
              {remove.isError ? (
                <p className="max-w-prose text-critical-text">{refusalSentence(remove.error)}</p>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <Button
                  pending={remove.isPending}
                  onClick={() =>
                    remove.mutate(undefined, {
                      onSuccess: () => void navigate({ to: "/settings/runbooks" }),
                    })
                  }
                >
                  {DELETE}
                </Button>
                <Button variant="secondary" onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
              </div>
            </ConfirmPanel>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => setEditing(true)}>
                {EDIT}
              </Button>
              <Button variant="secondary" onClick={() => setConfirming(true)}>
                {DELETE}
              </Button>
            </div>
          )
        ) : null}
      </div>
    </SettingsLayout>
  );
}
