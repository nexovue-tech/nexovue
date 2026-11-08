import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";

import { useSloHistory } from "./api";
import { historyChanges, historyHeading } from "./month-words";
import { timeIn } from "./slo-words";

/** *History* on a target's page (F19 FR30, BR7): every change, from what to what, when,
 * and by whom where the reader may know. */
export function SloHistoryTab({ sloId, zone }: { sloId: string; zone: string }) {
  const history = useSloHistory(sloId);
  if (history.isPending) return <SkeletonRows rows={3} label="Loading the history" />;
  if (history.isError) {
    return (
      <InlineFailure
        title="The history could not be loaded"
        error={history.error}
        onRetry={() => history.refetch()}
      />
    );
  }
  if (history.data.length === 0) {
    return <p className="py-3 text-ink-dim text-sm">No change recorded yet.</p>;
  }
  return (
    <ol className="flex flex-col divide-y divide-line py-1">
      {history.data.map((entry) => (
        <li key={`${entry.at}-${entry.what}`} className="flex flex-col gap-1 py-3 text-sm">
          <p className="text-ink">
            <span className="font-medium">{historyHeading(entry)}</span>
            <span className="text-ink-dim">, {timeIn(entry.at, zone)}</span>
          </p>
          <ul className="flex max-w-prose flex-col gap-0.5 text-ink-dim">
            {historyChanges(entry).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </li>
      ))}
    </ol>
  );
}
