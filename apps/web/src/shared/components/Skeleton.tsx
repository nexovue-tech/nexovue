/**
 * Loading placeholders. Skeletons, not spinners: a spinner says "wait", a skeleton says
 * "here is the shape of what is coming", and this product's shape is rows of text.
 */

interface SkeletonTextProps {
  /** Fraction of the line the placeholder fills, so a block does not read as a bar chart. */
  width?: "full" | "wide" | "half" | "short";
}

const WIDTHS: Record<NonNullable<SkeletonTextProps["width"]>, string> = {
  full: "w-full",
  wide: "w-3/4",
  half: "w-1/2",
  short: "w-24",
};

export function SkeletonText({ width = "full" }: SkeletonTextProps) {
  return <span className={`block h-3 animate-pulse rounded-xs bg-raised ${WIDTHS[width]}`} />;
}

interface SkeletonRowsProps {
  rows?: number;
  label?: string;
}

/**
 * Rows are described up front rather than keyed by loop index: a placeholder row has no
 * identity of its own, so the description carries one and the widths stop looking like a
 * bar chart.
 */
function describeRows(rows: number) {
  return Array.from({ length: rows }, (_, position) => ({
    id: `skeleton-row-${position}`,
    width: position % 3 === 0 ? ("wide" as const) : ("half" as const),
  }));
}

export function SkeletonRows({ rows = 8, label = "Loading" }: SkeletonRowsProps) {
  return (
    <div className="flex flex-col" role="status" aria-live="polite" aria-label={label}>
      {describeRows(rows).map((row) => (
        <div
          key={row.id}
          className="flex items-center gap-6 border-line-subtle border-b px-4 py-2.5"
        >
          <span className="w-16 shrink-0">
            <SkeletonText width="full" />
          </span>
          <span className="flex-1">
            <SkeletonText width={row.width} />
          </span>
          <span className="w-20 shrink-0">
            <SkeletonText width="full" />
          </span>
        </div>
      ))}
    </div>
  );
}
