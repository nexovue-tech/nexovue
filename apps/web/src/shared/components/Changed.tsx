import type { ReactNode } from "react";

import { useJustChanged } from "@/shared/live-updates";

/**
 * Marks the cell whose reading just moved.
 *
 * **The cell, not the row.** A row that lights up because one of six numbers changed
 * sends a reader hunting for which one — the question marking a change exists to answer.
 *
 * A background wash rather than a border or a size change: it does not move anything, so
 * a value that changes while somebody is reading the value beside it disturbs neither.
 * Silent under `prefers-reduced-motion`, which `useJustChanged` decides.
 */
export function Changed({ value, children }: { value: unknown; children: ReactNode }) {
  const justChanged = useJustChanged(value);

  return (
    <span
      className={
        justChanged
          ? "rounded-sm bg-brand-surface transition-colors duration-700"
          : "transition-colors duration-700"
      }
    >
      {children}
    </span>
  );
}
