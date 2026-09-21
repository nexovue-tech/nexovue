import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { formatTimestamp } from "@/shared/format/time";
import { useIsOperator } from "@/shared/identity";

import { needsReAuthentication, useOrganizations } from "./api";
import { ConfirmItIsYou } from "./ConfirmItIsYou";
import { NotTheOperator } from "./NotTheOperator";

/**
 * Every customer, from nexovue's side (`docs/changes/super-admin-in-the-console/`).
 *
 * Inside the console rather than beside it: one sign-in, one session, and the
 * separation that matters is the role plus a re-authentication. Nothing here is
 * reachable without both, on the screen or in the API.
 *
 * Left-aligned at `px-4` like every other screen, and **uncapped like every other
 * screen**: centred, these started 170px to the right of the one a reader came
 * from, and a `4xl` cap then left a third of a wide window empty beside a table
 * with five columns in it (`docs/quality/console-review-2026-09-20.md`). The
 * header holds and the list scrolls under it, which is what Services, Traces and
 * Logs already do.
 */
export function PlatformScreen() {
  const operator = useIsOperator();
  const [typed, setTyped] = useState("");
  const organizations = useOrganizations(typed);
  const narrowed = typed.trim().length > 1;
  const rows = organizations.data?.organizations ?? [];
  const truncated = organizations.data?.truncated ?? false;

  if (!operator) return <NotTheOperator />;
  if (needsReAuthentication(organizations.error)) {
    return <ConfirmItIsYou onConfirmed={() => void organizations.refetch()} />;
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Platform</h1>
            <p className="mt-1 text-ink-dim text-sm">
              Every organization on this deployment, and what nexovue can do about them.
            </p>
          </div>
          <Link to="/admin/onboard" className="font-medium text-brand-text text-sm hover:underline">
            Onboard a customer
          </Link>
        </div>

        {/* The hint is a line under the field rather than inside it, so the box
            keeps its own height and the row below does not move when the hint
            wraps (`docs/changes/console-review-fixes/`). */}
        <div className="mt-4 max-w-md">
          <TextField label="Find an organization" name="q" value={typed} onChange={setTyped} />
        </div>
        <p className="mt-1.5 text-ink-dim text-xs">
          Narrows the list below — by name, slug, id, or a member's email address.
        </p>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-10">
        {typed.trim().length === 1 ? (
          <EmptyState
            title="Two characters or more"
            detail="One matches most of the list. Clear the box to see every customer again."
          />
        ) : organizations.isPending ? (
          <SkeletonRows rows={5} label="Loading customers" />
        ) : organizations.isError ? (
          <InlineFailure
            title={narrowed ? "The search failed" : "The customers could not be listed"}
            error={organizations.error}
            onRetry={() => void organizations.refetch()}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            title={narrowed ? "Nothing matched" : "No customers yet"}
            detail={
              narrowed
                ? "No organization matches that. A member's own email address finds theirs."
                : "Onboard the first one after they pay on the website."
            }
          />
        ) : (
          <>
            {/* The widths are declared once and shared by the heading row and every
              row under it, so a heading cannot drift off its column — the defect
              the console review found on Traces and Errors. */}
            <div className="flex items-center gap-3 border-line-subtle border-b px-1 pb-1 font-mono text-2xs text-ink-dim uppercase tracking-caps">
              <span className="min-w-0 flex-1">Organization</span>
              <span className="hidden w-28 shrink-0 sm:block">Region</span>
              <span className="w-20 shrink-0">Plan</span>
              <span className="hidden w-32 shrink-0 md:block">Created</span>
              <span className="w-28 shrink-0 text-right">State</span>
            </div>

            <ul aria-label="Organizations">
              {rows.map((row) => (
                <li key={row.id} className="border-line-subtle border-b">
                  <Link
                    to="/admin/organizations/$orgId"
                    params={{ orgId: row.id }}
                    className="flex items-center gap-3 px-1 py-2.5 text-sm hover:bg-hover"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="font-medium text-ink">{row.name}</span>{" "}
                      <span className="font-mono text-2xs text-ink-dim">{row.slug}</span>
                    </span>
                    <span className="hidden w-28 shrink-0 text-ink-dim sm:block">{row.region}</span>
                    <span className="w-20 shrink-0 text-ink-dim">{row.plan}</span>
                    <span className="hidden w-32 shrink-0 font-mono text-2xs text-ink-dim md:block">
                      {formatTimestamp(row.created_at)}
                    </span>
                    <span className="w-28 shrink-0 text-right">
                      {row.deleted ? (
                        <span className="text-critical-text">deleted</span>
                      ) : row.suspended ? (
                        <span className="text-warning-text">suspended</span>
                      ) : (
                        <span className="text-ink-dim">active</span>
                      )}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>

            <p className="mt-3 text-ink-dim text-xs">
              {truncated
                ? `The newest ${rows.length}. Search to reach the rest.`
                : `${rows.length} ${rows.length === 1 ? "customer" : "customers"}.`}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
