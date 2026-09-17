import type { ReactNode } from "react";

/**
 * Nothing to show is a sentence, not a blank area. It says what would be here and what
 * would put it here — an empty screen that explains nothing reads as a broken one.
 */
export function EmptyState({
  title,
  detail,
  action,
  headingLevel = 2,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
  /**
   * Where this sits in the page's outline. A screen's own empty state follows the
   * `h1` title, but one inside a section has to sit under that section's heading —
   * an `h2` nested in an `h3` reads to a screen reader as a new top-level part of
   * the page.
   */
  headingLevel?: 2 | 3 | 4;
}) {
  const Heading = `h${headingLevel}` as const;
  return (
    <div className="mx-auto max-w-lg px-8 py-16 text-center">
      <Heading className="font-medium font-serif text-ink text-lg tracking-tight">{title}</Heading>
      <p className="mt-2 text-ink-dim text-sm leading-relaxed">{detail}</p>
      {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
    </div>
  );
}
