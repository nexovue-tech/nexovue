import { useQueryClient } from "@tanstack/react-query";

import { Button } from "@/shared/components/Button";
import { CopyButton } from "@/shared/components/CopyButton";
import { InlineFailure } from "@/shared/components/InlineFailure";

import type { CaseLink, TimelineResponse } from "./api";
import { timelineQueryKey, useRemoveLink, useRetryLink } from "./api";
import { linkStatusWords, myNotesText } from "./link-words";

const PROSE = "max-w-prose";

/**
 * A case's tracker issues (F22 T4.7): each by its key and the address nexovue built,
 * with its status in words that change with age (AC119) and read by reader (AC116).
 * Retry is offered only where it can help (FR44). After an issue exists, the person is
 * pointed at it to assign someone there, since an issue notifies nobody until then, and
 * may copy their own notes to paste under their own name (FR37, FR51, AC127).
 */
export function LinksList({
  findingId,
  links,
  caseActive,
  admin,
  canOperate,
  viewerId,
  zone,
  now,
}: {
  findingId: string;
  links: CaseLink[];
  caseActive: boolean;
  admin: boolean;
  canOperate: boolean;
  viewerId: string | null;
  zone?: string;
  now?: number;
}) {
  const retry = useRetryLink(findingId);
  const remove = useRemoveLink(findingId);
  const queryClient = useQueryClient();
  if (links.length === 0) return null;
  const synced = links.find((link) => link.role === "synced" && link.sync_status !== "stopped");
  // FR51: from the timeline the page already read; nothing is fetched to build it.
  const timeline = queryClient.getQueryData<TimelineResponse>(timelineQueryKey(findingId));
  const myNotes = myNotesText(timeline?.events ?? [], viewerId);

  return (
    <div className="flex flex-col gap-2" data-testid="case-links">
      <ul className="flex flex-col gap-1">
        {links.map((link) => (
          <li key={link.id} className={`text-ink text-sm ${PROSE}`}>
            <span className="text-ink-dim">
              {link.role === "synced" ? "Issue: " : "Also linked: "}
            </span>
            {link.url != null && link.external_key != null ? (
              <a
                href={link.url}
                target="_blank"
                rel="noreferrer noopener"
                className="font-mono text-brand-text underline-offset-2 hover:underline"
              >
                {link.external_key}
              </a>
            ) : null}
            <span>
              {" "}
              {link.url != null ? "· " : ""}
              {linkStatusWords(link, { caseActive, admin, now, zone })}
            </span>
            {canOperate && link.retry_helps ? (
              <Button
                variant="secondary"
                className="ml-2"
                pending={retry.isPending && retry.variables === link.id}
                pendingLabel="Trying again"
                onClick={() => retry.mutate(link.id)}
              >
                Try again
              </Button>
            ) : null}
            {canOperate && link.sync_status !== "creating" ? (
              <Button
                variant="secondary"
                className="ml-2"
                pending={remove.isPending && remove.variables === link.id}
                pendingLabel="Removing"
                onClick={() => remove.mutate(link.id)}
              >
                Remove here
              </Button>
            ) : null}
          </li>
        ))}
      </ul>
      {canOperate && synced?.url != null && synced.external_key != null ? (
        <div className="flex flex-col gap-1">
          <a
            href={synced.url}
            target="_blank"
            rel="noreferrer noopener"
            className={`font-medium text-brand-text text-sm underline-offset-2 hover:underline ${PROSE}`}
          >
            Open {synced.external_key} to assign it
          </a>
          <p className={`text-ink-dim text-sm ${PROSE}`}>
            An issue notifies nobody until someone is assigned.
          </p>
          {myNotes ? <CopyButton value={myNotes} label="my notes" /> : null}
        </div>
      ) : null}
      {retry.isError ? <InlineFailure title="That did not go through" error={retry.error} /> : null}
      {remove.isError ? (
        <InlineFailure title="The issue could not be removed here" error={remove.error} />
      ) : null}
    </div>
  );
}
