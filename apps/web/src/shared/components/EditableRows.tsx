import { Plus, X } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef } from "react";

import { Button } from "./Button";

/**
 * A list the reader edits in place: add a row, change its fields, remove it.
 *
 * Rows are keyed by `row.key`, never by index, so removing row 2 does not hand row 3's
 * half-typed input to React as if it were row 2's. Focus follows the edit — into the row
 * just added, or onto what took the removed row's place — because a keyboard user whose
 * focus falls back to the page body has lost their place in the form.
 */

/** What can take focus inside a row, in document order. */
const FOCUSABLE =
  'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), a[href], [contenteditable="true"], [tabindex]:not([tabindex="-1"])';

/** Where focus goes once the parent has applied an add or a remove. */
type PendingFocus =
  | { kind: "added"; key: string }
  | { kind: "removed"; key: string; nextKey: string | undefined };

export function EditableRows<Row extends { key: string }>({
  label,
  rows,
  onChange,
  newRow,
  renderRow,
  addLabel,
  emptyLabel,
  max,
  rowErrors,
}: {
  /** Accessible name for the list, e.g. "Headers". */
  label: string;
  rows: readonly Row[];
  onChange: (rows: Row[]) => void;
  /** A new blank row. Its `key` must be fresh — callers use crypto.randomUUID(). */
  newRow: () => Row;
  /** One row's editable fields. `update` replaces this row. */
  renderRow: (row: Row, update: (next: Row) => void, index: number) => ReactNode;
  addLabel: string;
  /** Shown when there are no rows. */
  emptyLabel?: string;
  /** Add is disabled at the limit, with the limit stated. */
  max?: number;
  /** Per-row problem, keyed by row index, shown under that row. */
  rowErrors?: Readonly<Record<number, string>>;
}) {
  const baseId = useId();
  const rowElements = useRef(new Map<string, HTMLLIElement>());
  const addArea = useRef<HTMLDivElement>(null);
  const pendingFocus = useRef<PendingFocus | null>(null);

  const atLimit = max !== undefined && rows.length >= max;

  // Runs after every render: the parent owns `rows`, so focus can only move once it has
  // actually applied the change, which may not be the render straight after the click.
  useEffect(() => {
    const pending = pendingFocus.current;
    if (!pending) return;
    const hasKey = rows.some((row) => row.key === pending.key);

    if (pending.kind === "added") {
      if (!hasKey) return;
      pendingFocus.current = null;
      focusFirstIn(rowElements.current.get(pending.key));
      return;
    }

    if (hasKey) return;
    pendingFocus.current = null;
    const next =
      pending.nextKey === undefined ? undefined : rowElements.current.get(pending.nextKey);
    if (next) focusFirstIn(next);
    else addArea.current?.querySelector("button")?.focus();
  });

  function add() {
    if (atLimit) return;
    const row = newRow();
    pendingFocus.current = { kind: "added", key: row.key };
    onChange([...rows, row]);
  }

  function remove(index: number) {
    const removed = rows[index];
    if (!removed) return;
    pendingFocus.current = { kind: "removed", key: removed.key, nextKey: rows[index + 1]?.key };
    onChange(rows.filter((_, position) => position !== index));
  }

  function update(index: number, next: Row) {
    onChange(rows.map((row, position) => (position === index ? next : row)));
  }

  return (
    <div className="flex flex-col gap-2">
      {rows.length === 0 ? (
        emptyLabel ? (
          <p className="text-ink-dim text-sm">{emptyLabel}</p>
        ) : null
      ) : (
        <ul aria-label={label} className="flex flex-col gap-2">
          {rows.map((row, index) => {
            const position = index + 1;
            const error = rowErrors?.[index];
            const errorId = `${baseId}-row-${index}-error`;
            return (
              <li
                key={row.key}
                ref={(element) => {
                  if (element) rowElements.current.set(row.key, element);
                  else rowElements.current.delete(row.key);
                }}
                className="flex flex-col gap-1"
              >
                <div className="flex items-start gap-2">
                  {/* The fields stack on a narrow screen and sit in a line from `sm` up. */}
                  <fieldset
                    aria-label={`Row ${position}`}
                    aria-describedby={error ? errorId : undefined}
                    className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-start"
                  >
                    {renderRow(row, (next) => update(index, next), index)}
                  </fieldset>
                  <button
                    type="button"
                    onClick={() => remove(index)}
                    aria-label={`Remove row ${position}`}
                    title={`Remove row ${position}`}
                    className="mt-1 shrink-0 rounded-sm p-1 text-ink-dim hover:bg-hover hover:text-ink focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
                  >
                    <X size={14} aria-hidden="true" />
                  </button>
                </div>
                {error ? (
                  <p id={errorId} className="text-critical-text text-xs">
                    {error}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      <div ref={addArea} className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Button variant="secondary" onClick={add} disabled={atLimit}>
          <span className="flex items-center gap-1.5">
            <Plus size={12} aria-hidden="true" />
            {addLabel}
          </span>
        </Button>
        {atLimit ? (
          <p className="text-ink-dim text-xs">{`${label} holds at most ${max} rows.`}</p>
        ) : null}
      </div>
    </div>
  );
}

function focusFirstIn(container: HTMLElement | undefined) {
  container?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
}
