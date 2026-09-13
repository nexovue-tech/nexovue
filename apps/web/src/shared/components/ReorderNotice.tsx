import { formatCount } from "@/shared/format/number";

/**
 * "The ranking moved while you were reading; take it when you are ready."
 *
 * Offered rather than applied, because applying it is the harm: a list that reorders
 * under a cursor already reaching for a row is how somebody clicks the wrong service.
 * The readings inside the rows are already current — only their order is being held.
 */
export function ReorderNotice({ onApply, count }: { onApply: () => void; count?: number }) {
  return (
    <div className="flex items-baseline gap-2 border-line-subtle border-b bg-surface px-4 py-1.5">
      <span className="text-ink-dim text-xs">
        {count === undefined
          ? "The ranking has changed while you were reading."
          : `The ranking has changed across ${formatCount(count)} rows while you were reading.`}
      </span>
      <button
        type="button"
        onClick={onApply}
        className="text-brand-text text-xs underline-offset-2 hover:underline"
      >
        Re-sort
      </button>
    </div>
  );
}
