import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { useCan } from "@/shared/identity";
import { writtenAge } from "@/shared/runbook-age";
import { useSelection } from "@/shared/selection";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import { type RunbookSummary, useRunbooks } from "./api";
import { RunbookForm } from "./RunbookForm";
import { ADD, capReached, DESCRIPTION, EMPTY, FORMER_MEMBER, TITLE, UPLOAD } from "./runbook-words";
import { UploadForm } from "./UploadForm";

/**
 * `/settings/runbooks` (F4 T5.3; FR32): the selected project's runbooks, with their service,
 * author and age, a warning tone past 180 days. Every signed-in person reads; only
 * `manage_knowledge` adds, uploads, edits and deletes (D48). Absent where AI is not offered.
 */
export function RunbooksScreen() {
  const { projectId } = useSelection();
  const list = useRunbooks(projectId);
  const may = useCan("manage_knowledge");
  const [open, setOpen] = useState<"add" | "upload" | null>(null);
  const now = Date.now();
  return (
    <SettingsLayout title={TITLE} description={DESCRIPTION}>
      {list.isPending ? (
        <SkeletonRows rows={4} label="Loading the runbooks" />
      ) : list.isError ? (
        <InlineFailure
          title="The runbooks could not be loaded"
          error={list.error}
          onRetry={() => void list.refetch()}
        />
      ) : (
        <div className="flex flex-col gap-4">
          {may && projectId ? (
            list.data.runbooks.length >= list.data.limit ? (
              <p className="max-w-prose text-ink text-sm">{capReached(list.data.limit)}</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={() => setOpen(open === "add" ? null : "add")}>
                  {ADD}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => setOpen(open === "upload" ? null : "upload")}
                >
                  {UPLOAD}
                </Button>
              </div>
            )
          ) : null}
          {open === "add" && projectId ? (
            <RunbookForm projectId={projectId} onDone={() => setOpen(null)} />
          ) : null}
          {open === "upload" && projectId ? (
            <UploadForm projectId={projectId} onDone={() => setOpen(null)} />
          ) : null}
          {list.data.runbooks.length === 0 ? (
            <p className="max-w-prose text-ink-dim text-sm">{EMPTY}</p>
          ) : (
            <ul aria-label="Runbooks" className="flex flex-col divide-y divide-line">
              {list.data.runbooks.map((runbook) => (
                <Row key={runbook.id} runbook={runbook} now={now} />
              ))}
            </ul>
          )}
        </div>
      )}
    </SettingsLayout>
  );
}

function Row({ runbook, now }: { runbook: RunbookSummary; now: number }) {
  const age = writtenAge(runbook.updated_at, now);
  return (
    <li className="flex flex-col gap-0.5 py-2">
      <Link
        to="/settings/runbooks/$runbookId"
        params={{ runbookId: runbook.id }}
        className="break-words font-medium text-brand-text text-sm underline-offset-2 hover:underline"
      >
        {runbook.title}
      </Link>
      <p className="text-ink-dim text-xs">
        {runbook.service_name ? (
          <span className="break-words font-mono">{runbook.service_name} · </span>
        ) : null}
        {runbook.author ?? FORMER_MEMBER} ·{" "}
        <span className={age.stale ? "text-warning-text" : undefined}>{age.words}</span>
      </p>
    </li>
  );
}
