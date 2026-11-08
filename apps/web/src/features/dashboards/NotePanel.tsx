import { visibleEscapes } from "@/shared/format/visible-escapes";

/**
 * A note is plain text, always (BR10, AC36): no Markdown, no HTML, no link made from a
 * URL. It needs no read, so it draws through any ClickHouse outage.
 */
export function NotePanel({ text }: { text: string }) {
  return (
    <p className="whitespace-pre-wrap break-words text-ink text-sm">
      {visibleEscapes(text, { keepLineBreaks: true })}
    </p>
  );
}
