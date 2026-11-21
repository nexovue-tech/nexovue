import { useIsHosted } from "@/shared/deployment";
import { visibleEscapes } from "@/shared/format/visible-escapes";

/**
 * A note is plain text, always (BR10, AC36): no Markdown, no HTML, no link made from a
 * URL. It needs no read, so it draws through any ClickHouse outage.
 *
 * Under it, who can read it and what not to paste (FR62, AC119): on the hosted service,
 * support too, while an owner has granted access; on a self-hosted install there is no
 * support to mention. Both say an owner or admin can purge text at once (FR68).
 */

const READERS_SELF_HOSTED = "Everyone in this organization can read this.";
const READERS_HOSTED =
  "Everyone in this organization can read this, and support while an owner has granted access.";
const DO_NOT_PASTE = "Do not paste secrets or personal details.";
const PURGE = "An owner or admin can purge text at once.";

export function noteWarning(hosted: boolean): string {
  return `${hosted ? READERS_HOSTED : READERS_SELF_HOSTED} ${DO_NOT_PASTE} ${PURGE}`;
}

export function NotePanel({ text }: { text: string }) {
  const hosted = useIsHosted();
  return (
    <>
      <p className="whitespace-pre-wrap break-words text-ink text-sm">
        {visibleEscapes(text, { keepLineBreaks: true })}
      </p>
      <p className="mt-2 text-ink-dim text-xs">{noteWarning(hosted)}</p>
    </>
  );
}
