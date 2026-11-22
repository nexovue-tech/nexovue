import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { apiClient, isForbidden } from "@/shared/api-client";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatTimestamp } from "@/shared/format/time";
import { useCan } from "@/shared/identity";

/* F5 Step 9 (plan T9.3; spec UR12, AC39; design, *The summary*): suggested fixes over the last
 * 30 days, beside F3's usage on `/settings/ai`. Counts only, with no claim of value. Each form
 * is stated: loading, failed, empty, no permission. A count the API did not give reads as an
 * em dash, and 0 only as a true count. A notice says which of Nexovue's rules withheld a
 * suggestion and the files it read, never the suggestion's text. */

export type FixVerdictKind = "merge_as_is" | "useful_start" | "wrong";

export interface FixSummaryNotice {
  rule: string;
  sentence: string;
  at: string;
  fix_id: string | null;
  finding_id: string | null;
  /** Null in a support view, which reads no paths. */
  files: string[] | null;
}

export interface FixSummary {
  days: number;
  empty: string;
  requested: number;
  refused: { reason: string; label: string; count: number }[];
  verdicts: { verdict: FixVerdictKind; label: string; count: number }[];
  drafts_opened: number;
  drafts_merged: number;
  tokens: number;
  notices: FixSummaryNotice[];
}

export const FIX_SUMMARY_WORDS = {
  heading: "Suggested fixes, last 30 days",
  loading: "Loading the suggested fix summary",
  failed: "The suggested fix summary could not be loaded",
  noPermission: "Only owners and admins read the suggested fix summary.",
  requested: "Asked for",
  refused: "Refused, by reason",
  noRefusals: "None refused.",
  verdicts: "Was this suggestion useful?",
  draftsOpened: "Drafts opened",
  draftsMerged: "Drafts merged",
  tokens: "Tokens used",
  notices: "Notices",
  files: "Files read",
  filesHidden: "Files are not shown in a support view.",
  finding: "Open the finding",
} as const;

const UNKNOWN = "—";

/** A true count as a number; anything the API did not give as an em dash (AC39). */
export function countText(value: number | null | undefined): string {
  return typeof value === "number" ? value.toLocaleString("en-US") : UNKNOWN;
}

export function useFixSummary(enabled: boolean) {
  return useQuery<FixSummary>({
    queryKey: ["ai", "fix-summary"],
    enabled,
    queryFn: ({ signal }) => apiClient.get<FixSummary>("ai/fix-summary", { signal }),
  });
}

export function FixSummaryPanel() {
  const allowed = useCan("manage_ai");
  const summary = useFixSummary(allowed);
  if (!allowed) return <NoPermission />;
  if (summary.isPending) return <SkeletonRows rows={3} label={FIX_SUMMARY_WORDS.loading} />;
  if (summary.isError) {
    return isForbidden(summary.error) ? (
      <NoPermission />
    ) : (
      <InlineFailure
        title={FIX_SUMMARY_WORDS.failed}
        error={summary.error}
        onRetry={() => void summary.refetch()}
      />
    );
  }
  const view = summary.data;
  if (view.requested === 0 && view.notices.length === 0) {
    return <p className="max-w-prose text-ink-dim text-sm">{view.empty}</p>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-3 text-sm" data-testid="fix-summary">
      <h3 className="font-medium text-ink">{FIX_SUMMARY_WORDS.heading}</h3>
      <ul className="flex flex-col gap-1 text-ink">
        <li>{`${FIX_SUMMARY_WORDS.requested}: ${countText(view.requested)}`}</li>
        <li>{`${FIX_SUMMARY_WORDS.draftsOpened}: ${countText(view.drafts_opened)}`}</li>
        <li>{`${FIX_SUMMARY_WORDS.draftsMerged}: ${countText(view.drafts_merged)}`}</li>
        <li>{`${FIX_SUMMARY_WORDS.tokens}: ${countText(view.tokens)}`}</li>
      </ul>
      <section aria-label={FIX_SUMMARY_WORDS.refused} className="flex flex-col gap-1">
        <h4 className="text-ink-dim">{FIX_SUMMARY_WORDS.refused}</h4>
        {view.refused.length === 0 ? (
          <p className="text-ink">{FIX_SUMMARY_WORDS.noRefusals}</p>
        ) : (
          <ul className="flex flex-col gap-1 text-ink">
            {view.refused.map((each) => (
              <li key={each.reason} className="[overflow-wrap:anywhere]">
                {`${each.label}: ${countText(each.count)}`}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-label={FIX_SUMMARY_WORDS.verdicts} className="flex flex-col gap-1">
        <h4 className="text-ink-dim">{FIX_SUMMARY_WORDS.verdicts}</h4>
        <ul className="flex flex-col gap-1 text-ink">
          {view.verdicts.map((each) => (
            <li key={each.verdict}>{`${each.label}: ${countText(each.count)}`}</li>
          ))}
        </ul>
      </section>
      {view.notices.length > 0 ? (
        <ol aria-label={FIX_SUMMARY_WORDS.notices} className="flex flex-col gap-3">
          {view.notices.map((notice, index) => (
            <Notice key={`${notice.fix_id ?? index}-${notice.at}`} notice={notice} />
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function Notice({ notice }: { notice: FixSummaryNotice }) {
  return (
    <li className="flex min-w-0 flex-col gap-1 rounded-sm border border-line p-3">
      <p className="max-w-prose text-warning-text">{notice.sentence}</p>
      <p className="text-ink-dim">{formatTimestamp(notice.at)}</p>
      {notice.files === null ? (
        <p className="text-ink-dim">{FIX_SUMMARY_WORDS.filesHidden}</p>
      ) : notice.files.length > 0 ? (
        <p className="text-ink">
          {`${FIX_SUMMARY_WORDS.files}: `}
          <span className="font-mono [overflow-wrap:anywhere]">{notice.files.join(", ")}</span>
        </p>
      ) : null}
      {notice.finding_id ? (
        <Link
          className="inline-flex min-h-11 items-center text-ink underline"
          to="/findings/$findingId"
          params={{ findingId: notice.finding_id }}
        >
          {FIX_SUMMARY_WORDS.finding}
        </Link>
      ) : null}
    </li>
  );
}

function NoPermission() {
  return <p className="max-w-prose text-ink-dim text-sm">{FIX_SUMMARY_WORDS.noPermission}</p>;
}
