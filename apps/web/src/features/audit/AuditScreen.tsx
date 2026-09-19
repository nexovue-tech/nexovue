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
import type { AuditFilters, EventKind } from "./api";
import { useAuditLog, usePeople } from "./api";
import { describeEntry, KIND_LABELS, RANGES } from "./audit-words";

const KINDS = new Set<string>(["people", "credentials", "sign_in", "configuration", "support"]);

function isKind(value: string): value is EventKind {
  return KINDS.has(value);
}

/**
 * Who did what in this organization, newest first (F26, FR36): people coming and going,
 * keys and secrets, sign-ins, configuration, and anything nexovue support did while
 * viewing as someone. For the owners and admins who answer for the organization.
 */
export function AuditScreen() {
  const allowed = useCan("read_audit");
  const session = useSession();
  const [actor, setActor] = useState("");
  const [kind, setKind] = useState("");
  const [range, setRange] = useState("7d");

  const hours = RANGES.find((each) => each.id === range)?.hours ?? null;
  const filters: AuditFilters = {
    ...(actor ? { actor } : {}),
    ...(isKind(kind) ? { kind } : {}),
    ...(hours !== null ? { from: startOfRange(hours) } : {}),
  };
  const log = useAuditLog(filters, allowed);
  const people = usePeople(allowed);

  return (
    <SettingsLayout
      title="Audit log"
      description="Every change to who is in this organization, what they may do, its keys and its configuration — and every page nexovue support viewed on your behalf. Kept, not trimmed."
    >
      {!allowed ? (
        session.isPending ? (
          <SkeletonRows rows={3} label="Loading" />
        ) : (
          <Refused role={session.data?.user.role} who="an owner or an admin" />
        )
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-baseline gap-x-6 gap-y-2">
            <SelectField
              label="Who"
              value={actor}
              options={[
                { id: "", label: "Anyone" },
                ...(people.data ?? []).map((person) => ({
                  id: person.id,
                  label: person.name ?? person.email,
                })),
              ]}
              onChange={setActor}
              optionLabel={(option) => option.label}
              capped
            />
            <SelectField
              label="What"
              value={kind}
              options={KIND_LABELS}
              onChange={setKind}
              optionLabel={(option) => option.label}
            />
            <SelectField
              label="When"
              value={range}
              options={RANGES}
              onChange={setRange}
              optionLabel={(option) => option.label}
            />
          </div>

          {log.isPending ? (
            <SkeletonRows rows={5} label="Loading the audit log" />
          ) : log.isError ? (
            <InlineFailure
              title="The audit log could not be loaded"
              error={log.error}
              onRetry={() => void log.refetch()}
            />
          ) : log.data.pages[0]?.entries.length === 0 ? (
            <EmptyState
              headingLevel={3}
              title="Nothing in this range"
              detail="No one did anything matching these filters. Widen the range or clear a filter."
            />
          ) : (
            <>
              <ol className="border-line border-t" aria-label="Audit entries">
                {log.data.pages.flatMap((page) =>
                  page.entries.map((entry) => (
                    <li
                      key={entry.id}
                      className={`flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-line-subtle border-b py-2.5 ${entry.impersonation_context ? "border-l-2 border-l-warning-solid pl-3" : ""}`}
                    >
                      <p className="min-w-0 text-ink text-sm">
                        {describeEntry(entry)}
                        {entry.impersonation_context ? (
                          <span className="ml-2 font-mono text-2xs text-warning-text uppercase tracking-caps">
                            Support view
                          </span>
                        ) : null}
                      </p>
                      <time
                        dateTime={entry.at}
                        title={formatTimestamp(entry.at)}
                        className="shrink-0 text-ink-dim text-sm tabular-nums"
                      >
                        {formatRelativeTime(entry.at)}
                      </time>
                    </li>
                  )),
                )}
              </ol>
              {log.hasNextPage ? (
                <div className="mt-4">
                  <Button
                    variant="secondary"
                    pending={log.isFetchingNextPage}
                    pendingLabel="Loading…"
                    onClick={() => void log.fetchNextPage()}
                  >
                    Older entries
                  </Button>
                </div>
              ) : null}
            </>
          )}
        </>
      )}
    </SettingsLayout>
  );
}

function startOfRange(hours: number): string {
  // Rounded to the minute so the query key — and the cache — is stable across renders.
  const minute = 60_000;
  return new Date(Math.floor((Date.now() - hours * 3_600_000) / minute) * minute).toISOString();
}
