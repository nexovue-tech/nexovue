import type { ReactNode } from "react";

/**
 * Nothing to show is a sentence, not a blank area. It says what would be here and what
 * would put it here — an empty screen that explains nothing reads as a broken one.
 */
export function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-lg px-8 py-16 text-center">
      <h2 className="font-medium font-serif text-ink text-lg tracking-tight">{title}</h2>
      <p className="mt-2 text-ink-dim text-sm leading-relaxed">{detail}</p>
      {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
    </div>
  );
}
