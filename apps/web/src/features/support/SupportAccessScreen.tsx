import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { Refused } from "@/shared/components/Refused";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { useCan, useSession } from "@/shared/identity";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import type { GrantHours, SupportAccessState } from "./api";
import { useGrantSupportAccess, useSupportAccess, useWithdrawSupportAccess } from "./api";

const DURATIONS: readonly { id: string; label: string; hours: GrantHours }[] = [
  { id: "24", label: "24 hours", hours: 24 },
  { id: "72", label: "72 hours", hours: 72 },
  { id: "168", label: "7 days", hours: 168 },
];

/**
 * Whether nexovue support may view the console as your people, and every time it has
 * (F31, FR28). An owner's decision: support can see what that person sees, read-only,
 * for 30 minutes at a time, and every page it opens is in the audit log.
 */
export function SupportAccessScreen() {
  const allowed = useCan("grant_support");
  const session = useSession();
  const access = useSupportAccess(allowed);

  return (
    <SettingsLayout
      title="Support access"
      description="Let nexovue support see your console as one of your people, to help with a problem. Read-only, never more than 30 minutes at a time, and every page viewed is recorded."
    >
      {!allowed ? (
        session.isPending ? (
          <SkeletonRows rows={3} label="Loading" />
        ) : (
          <Refused role={session.data?.user.role} who="an owner" />
        )
      ) : access.isPending ? (
        <SkeletonRows rows={3} label="Loading support access" />
      ) : access.isError ? (
        <InlineFailure
          title="Support access could not be loaded"
          error={access.error}
          onRetry={() => void access.refetch()}
        />
      ) : (
        <div className="flex flex-col gap-8">
          <Access grantedUntil={access.data.granted_until} />
          <Views views={access.data.views} />
        </div>
      )}
    </SettingsLayout>
  );
}

function Access({ grantedUntil }: { grantedUntil: string | null }) {
  const [duration, setDuration] = useState("72");
  const grant = useGrantSupportAccess();
  const withdraw = useWithdrawSupportAccess();
  const hours = DURATIONS.find((each) => each.id === duration)?.hours ?? 72;

  return (
    <section aria-label="Access" className="flex flex-col gap-3">
      <p className="text-ink text-sm" role="status">
        {grantedUntil ? (
          <>
            nexovue support may view as your people until{" "}
            <time dateTime={grantedUntil}>{formatTimestamp(grantedUntil)}</time>.
          </>
        ) : (
          "nexovue support cannot view your console."
        )}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <SelectField
          label="For"
          value={duration}
          options={DURATIONS}
          onChange={setDuration}
          optionLabel={(option) => option.label}
        />
        <Button
          pending={grant.isPending}
          pendingLabel="Granting…"
          onClick={() => grant.mutate(hours)}
        >
          {/* A new window replaces the one in force, and ends any view inside it. */}
          {grantedUntil ? "Replace access" : "Grant access"}
        </Button>
        {grantedUntil ? (
          <Button
            variant="secondary"
            pending={withdraw.isPending}
            pendingLabel="Withdrawing…"
            onClick={() => withdraw.mutate(undefined)}
          >
            Withdraw now
          </Button>
        ) : null}
      </div>
      {(grant.error ?? withdraw.error) ? (
        <InlineFailure
          title="Support access did not change"
          error={grant.error ?? withdraw.error}
        />
      ) : null}
    </section>
  );
}

function Views({ views }: { views: SupportAccessState["views"] }) {
  return (
    <section aria-label="Support views">
      <h2 className="font-medium font-serif text-base">When support viewed</h2>
      {views.length === 0 ? (
        <EmptyState
          headingLevel={3}
          title="Never"
          detail="nexovue support has not viewed your console. Each view appears here with who, as whom and why."
        />
      ) : (
        <ol className="mt-2 border-line border-t">
          {views.map((view) => (
            <li
              key={view.impersonation_id ?? view.at}
              className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-line-subtle border-b py-2.5"
            >
              <p className="min-w-0 text-ink text-sm">
                {view.staff} viewed as {view.viewed ?? "someone"}
                {view.reason ? <span className="text-ink-dim"> — {view.reason}</span> : null}
              </p>
              <time
                dateTime={view.at}
                title={formatTimestamp(view.at)}
                className="shrink-0 text-ink-dim text-sm tabular-nums"
              >
                {formatRelativeTime(view.at)}
              </time>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
