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
 */
export function PlatformScreen() {
  const operator = useIsOperator();
  const [typed, setTyped] = useState("");
  const organizations = useOrganizations(typed);

  if (!operator) return <NotTheOperator />;
  if (needsReAuthentication(organizations.error)) {
    return <ConfirmItIsYou onConfirmed={() => void organizations.refetch()} />;
  }

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-4 py-6">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Platform</h1>
          <p className="mt-1 text-ink-dim text-sm">
            Every organization on this deployment, and what nexovue can do about them.
          </p>
        </div>
        <Link to="/admin/onboard" className="font-medium text-brand-text text-sm hover:underline">
          Onboard a customer
        </Link>
      </header>

      <TextField
        label="Find an organization"
        name="q"
        value={typed}
        onChange={setTyped}
        hint="By name, slug, id, or a member's email address."
      />

      {typed.trim().length <= 1 ? (
        <EmptyState
          title="Search to begin"
          detail="Two characters or more. A customer's own email address finds their organization."
        />
      ) : organizations.isPending ? (
        <SkeletonRows rows={3} label="Searching" />
      ) : organizations.isError ? (
        <InlineFailure
          title="The search failed"
          error={organizations.error}
          onRetry={() => void organizations.refetch()}
        />
      ) : organizations.data.length === 0 ? (
        <EmptyState title="Nothing matched" detail="No organization matches that." />
      ) : (
        <ul className="border-line border-t" aria-label="Organizations">
          {organizations.data.map((row) => (
            <li key={row.id} className="border-line-subtle border-b py-2.5">
              <Link
                to="/admin/organizations/$orgId"
                params={{ orgId: row.id }}
                className="font-medium text-ink hover:underline"
              >
                {row.name}
              </Link>
              <p className="mt-0.5 text-ink-dim text-xs">
                <span className="font-mono">{row.slug}</span> · {row.region} · plan {row.plan} ·
                created {formatTimestamp(row.created_at)}
                {row.suspended ? " · ingest suspended" : ""}
                {row.deleted ? " · deleted" : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
