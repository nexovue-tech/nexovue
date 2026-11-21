import { useRef, useState } from "react";

import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { CopyButton } from "@/shared/components/CopyButton";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { formatZonedTime } from "@/shared/format/zoned";
import { useCan, useSession } from "@/shared/identity";

import type { TimelineEvent } from "./api";
import { fetchEarlierTimeline, useDeleteNote, useTimeline } from "./api";
import { eventSentence } from "./finding-page";
import { EMPTY_TIMELINE, NOTE_DELETE_CONFIRMATION } from "./finding-words";

/** The newest rows shown before Show earlier: 20 wide, 5 on a phone (guesses). */
const FIRST_WIDE = 20;
const FIRST_NARROW = 5;
const PHONE = "(max-width: 640px)";

function firstPage(): number {
  try {
    return window.matchMedia(PHONE).matches ? FIRST_NARROW : FIRST_WIDE;
  } catch {
    return FIRST_WIDE;
  }
}

/**
 * The case's timeline (FR16, FR17): an ordered list, newest first, a day
 * heading whenever the date changes, **Show earlier** loading the next 100 with
 * focus moving to the first new row, and **Copy as text** carrying the notes.
 *
 * A note's words render as plain text with their line breaks: no Markdown, and
 * nothing in them is clickable (FR14, AC18). A failed read is this section's
 * failure alone — the page stands, and it never reads as "nothing happened".
 */
export function Timeline({ findingId, zone }: { findingId: string; zone?: string }) {
  const timeline = useTimeline(findingId);
  const deleteNote = useDeleteNote(findingId);
  const session = useSession();
  const mayDeleteAny = useCan("delete_any_case_note");
  const [shown, setShown] = useState(firstPage);
  const [earlier, setEarlier] = useState<TimelineEvent[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null | undefined>(undefined);
  const [armedDelete, setArmedDelete] = useState<string | null>(null);
  const firstNewRow = useRef<HTMLLIElement | null>(null);
  const focusAfterDelete = useRef<number | null>(null);

  if (timeline.isPending) {
    return <p className="text-ink-dim text-sm">Loading the timeline…</p>;
  }
  if (timeline.isError) {
    return (
      <InlineFailure
        title="The timeline could not be loaded"
        error={timeline.error}
        onRetry={() => void timeline.refetch()}
      />
    );
  }

  const loaded = [...timeline.data.events, ...earlier];
  const events = loaded.slice(0, shown);
  const hasEarlier = loaded.length > shown || (nextBefore ?? timeline.data.next_before) != null;
  const viewerId = session.data?.user.id;
  const inSupportView = Boolean(session.data?.support_view);

  const showEarlier = async () => {
    const already = loaded.length;
    if (loaded.length <= shown) {
      const before = nextBefore === undefined ? timeline.data.next_before : nextBefore;
      if (before != null) {
        const page = await fetchEarlierTimeline(findingId, before);
        setEarlier((rows) => [...rows, ...page.events]);
        setNextBefore(page.next_before);
      }
    }
    setShown(already + 100);
    setTimeout(() => firstNewRow.current?.focus(), 0);
  };

  if (events.length === 0) {
    return <p className="max-w-prose text-ink-dim text-sm">{EMPTY_TIMELINE}</p>;
  }

  const asText = loaded
    .map((event) => `${formatZonedTime(event.at, { zone })} — ${eventSentence(event)}`)
    .join("\n");

  let lastDay = "";
  const firstNewIndex = events.length > firstPage() ? firstPage() : -1;

  return (
    <div className="flex flex-col gap-2">
      {inSupportView ? null : <CopyButton value={asText} label="Copy as text" />}
      <ol className="flex max-w-prose flex-col gap-1.5">
        {events.map((event, index) => {
          const day = new Date(event.at).toDateString();
          const heading = day !== lastDay ? formatZonedTime(event.at, { zone }) : null;
          lastDay = day;
          const noteId =
            event.kind === "note_added" && typeof event.detail?.note_id === "string"
              ? event.detail.note_id
              : null;
          const mayDelete =
            noteId != null &&
            event.note_body != null &&
            !inSupportView &&
            (mayDeleteAny || (viewerId != null && event.actor_id === viewerId));
          return (
            <li
              key={event.id}
              tabIndex={-1}
              ref={(element) => {
                if (index === firstNewIndex) firstNewRow.current = element;
                if (index === focusAfterDelete.current) element?.focus();
              }}
              className="text-ink text-sm"
            >
              {heading ? (
                <p className="pt-2 font-mono text-2xs text-ink-dim uppercase tracking-caps">
                  {heading}
                </p>
              ) : null}
              <span className="text-ink-dim">{formatZonedTime(event.at, { zone })}</span>{" "}
              {/* Plain text with its line breaks: nothing a note says is markup. */}
              <span className="whitespace-pre-wrap">{eventSentence(event)}</span>
              {mayDelete ? (
                <Button variant="secondary" onClick={() => setArmedDelete(noteId)}>
                  Delete note
                </Button>
              ) : null}
              {armedDelete !== null && armedDelete === noteId ? (
                <ConfirmPanel label="Delete this note">
                  <p className="max-w-prose">{NOTE_DELETE_CONFIRMATION}</p>
                  <div className="flex gap-2">
                    <Button
                      pending={deleteNote.isPending}
                      pendingLabel="Deleting"
                      onClick={() => {
                        focusAfterDelete.current = index;
                        deleteNote.mutate(armedDelete, {
                          onSettled: () => setArmedDelete(null),
                        });
                      }}
                    >
                      Delete the note
                    </Button>
                    <Button variant="secondary" onClick={() => setArmedDelete(null)}>
                      Cancel
                    </Button>
                  </div>
                </ConfirmPanel>
              ) : null}
            </li>
          );
        })}
      </ol>
      {hasEarlier ? (
        <div>
          <Button variant="secondary" onClick={() => void showEarlier()}>
            Show earlier
          </Button>
        </div>
      ) : null}
    </div>
  );
}
