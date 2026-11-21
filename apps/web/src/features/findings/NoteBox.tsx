import { useId, useState } from "react";

import { ApiError } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";

import { useAddNote } from "./api";
import { NOTE_BOX_LABEL, NOTE_BOX_WARNING } from "./finding-words";

/** FR14's cap, refused inline beside the field rather than clamped silently. */
const NOTE_MAX_LENGTH = 4000;

/**
 * Write a note (FR14, AC104). Enter adds a line — a textarea's own behaviour —
 * and the button, or Ctrl or Cmd+Enter, sends. A failed send keeps the draft:
 * the field only empties on success.
 */
export function NoteBox({ findingId }: { findingId: string }) {
  const add = useAddNote(findingId);
  const [draft, setDraft] = useState("");
  const [refused, setRefused] = useState("");
  const describedBy = useId();

  const send = () => {
    if (!draft.trim()) return;
    if (draft.length > NOTE_MAX_LENGTH) {
      setRefused(
        `A note holds at most ${NOTE_MAX_LENGTH} characters; this one is ${draft.length}.`,
      );
      return;
    }
    setRefused("");
    add.mutate(draft, {
      onSuccess: () => setDraft(""),
      onError: (error) => {
        if (error instanceof ApiError && error.problem?.detail) {
          setRefused(error.problem.detail);
        }
      },
    });
  };

  return (
    <form
      className="flex max-w-prose flex-col gap-1.5"
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <label className="flex flex-col gap-1.5">
        <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
          {NOTE_BOX_LABEL}
        </span>
        <textarea
          rows={3}
          value={draft}
          aria-describedby={describedBy}
          // text-base on a phone: iOS Safari zooms on focus below 16 px.
          className="rounded border border-line bg-transparent px-2 py-1.5 text-base text-ink sm:text-sm"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
              event.preventDefault();
              send();
            }
          }}
        />
      </label>
      <p id={describedBy} className="text-2xs text-ink-dim">
        {NOTE_BOX_WARNING}
        {draft.length > NOTE_MAX_LENGTH * 0.9
          ? ` ${draft.length} of ${NOTE_MAX_LENGTH} characters.`
          : ""}
      </p>
      {refused ? (
        <p role="alert" className="text-sm text-warning-text">
          {refused}
        </p>
      ) : null}
      {add.isError && !refused ? (
        <p role="alert" className="text-sm text-warning-text">
          The note did not go through. It is still here — try again.
        </p>
      ) : null}
      <div>
        <Button type="submit" variant="secondary" pending={add.isPending} pendingLabel="Adding">
          Add note
        </Button>
      </div>
    </form>
  );
}
