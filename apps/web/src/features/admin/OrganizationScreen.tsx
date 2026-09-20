import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { useIsOperator } from "@/shared/identity";

import type { OrganizationPage } from "./api";
import {
  needsReAuthentication,
  useEndGrant,
  useGrantPlan,
  useOrganization,
  useResendOwnerLink,
  useResume,
  useSuspend,
  useViewAs,
} from "./api";
import { ConfirmItIsYou } from "./ConfirmItIsYou";
import { NotTheOperator } from "./NotTheOperator";

/** Plans that can be comped. The paid plan underneath is never rewritten (FR27). */
const GRANTABLE = ["pro", "scale"] as const;

export function OrganizationScreen({ orgId }: { orgId: string }) {
  const operator = useIsOperator();
  const page = useOrganization(orgId);

  if (!operator) return <NotTheOperator />;
  if (needsReAuthentication(page.error)) {
    return <ConfirmItIsYou onConfirmed={() => void page.refetch()} />;
  }
  if (page.isPending) return <SkeletonRows rows={6} label="Loading the organization" />;
  if (page.isError) {
    return (
      <InlineFailure
        title="The organization could not be loaded"
        error={page.error}
        onRetry={() => void page.refetch()}
      />
    );
  }

  const { organization, suspension, members, projects, usage } = page.data;
  return (
    <article
      aria-label={organization.name}
      className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-4 py-6"
    >
      <header>
        <Link to="/admin" className="text-ink-dim text-sm hover:text-ink">
          All organizations
        </Link>
        <h1 className="mt-2 font-semibold font-serif text-ink text-xl tracking-tight">
          {organization.name}
        </h1>
        <p className="text-ink-dim text-sm">
          <span className="font-mono">{organization.slug}</span> · {organization.region} · plan{" "}
          {usage?.plan.name ?? organization.plan} · created{" "}
          {formatTimestamp(organization.created_at)}
        </p>
      </header>

      <Ingest orgId={orgId} suspension={suspension} />
      <Plan orgId={orgId} paidPlan={organization.plan} grant={page.data.plan_grant} />

      {members.some((member) => member.role === "owner" && member.status === "invited") ? (
        <OwnerLink orgId={orgId} />
      ) : null}

      <section aria-label="Members">
        <h2 className="font-medium font-serif text-ink text-base">Members</h2>
        <ul className="mt-2 border-line border-t">
          {members.map((member) => (
            <li key={member.id} className="border-line-subtle border-b py-2 text-sm">
              {member.name ?? member.email}{" "}
              <span className="text-ink-dim">
                {member.email} · {member.role}
                {member.status === "invited" ? " · invited" : ""}
              </span>
              {member.status === "active" ? <ViewAs orgId={orgId} member={member} /> : null}
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Projects">
        <h2 className="font-medium font-serif text-ink text-base">Projects</h2>
        <ul className="mt-2 border-line border-t">
          {projects.map((project) => (
            <li key={project.id} className="border-line-subtle border-b py-2 text-sm">
              {project.name} <span className="font-mono text-ink-dim text-xs">{project.slug}</span>
              <span className="text-ink-dim"> · last data {project.last_ingest_day ?? "—"}</span>
            </li>
          ))}
        </ul>
      </section>
    </article>
  );
}

/** The stop valve (FR26): the same one `just suspend-close` closes, with a reason. */
function Ingest({
  orgId,
  suspension,
}: {
  orgId: string;
  suspension: OrganizationPage["suspension"];
}) {
  const [reason, setReason] = useState("");
  const suspend = useSuspend(orgId);
  const resume = useResume(orgId);

  return (
    <section aria-label="Ingest" className="flex flex-col gap-2">
      <h2 className="font-medium font-serif text-ink text-base">Ingest</h2>
      {suspension ? (
        <>
          <p className="text-critical-text text-sm">
            Suspended {formatRelativeTime(suspension.at)}: {suspension.reason}
          </p>
          <div>
            <Button
              variant="secondary"
              pending={resume.isPending}
              pendingLabel="Resuming…"
              onClick={() => resume.mutate(undefined)}
            >
              Resume ingest
            </Button>
          </div>
        </>
      ) : (
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (reason.trim()) suspend.mutate(reason.trim());
          }}
        >
          <p className="w-full text-ink-dim text-sm">Accepting data.</p>
          <div className="min-w-64 flex-1">
            <TextField
              label="Suspend, because"
              name="reason"
              value={reason}
              onChange={setReason}
              hint="The customer will ask why; this is what they are told."
            />
          </div>
          <div className="pb-1">
            <Button type="submit" pending={suspend.isPending} disabled={!reason.trim()}>
              Suspend ingest
            </Button>
          </div>
        </form>
      )}
      {(suspend.error ?? resume.error) ? (
        <InlineFailure title="The valve did not move" error={suspend.error ?? resume.error} />
      ) : null}
    </section>
  );
}

/** A complimentary plan until a date (FR27): in force until then, then the paid plan. */
function Plan({
  orgId,
  paidPlan,
  grant,
}: {
  orgId: string;
  paidPlan: string;
  grant: OrganizationPage["plan_grant"];
}) {
  const [plan, setPlan] = useState<string>(GRANTABLE[0]);
  const [services, setServices] = useState("3");
  const [endsOn, setEndsOn] = useState("");
  const [reason, setReason] = useState("");
  const give = useGrantPlan(orgId);
  const end = useEndGrant(orgId);

  return (
    <section aria-label="Plan" className="flex flex-col gap-2">
      <h2 className="font-medium font-serif text-ink text-base">Plan</h2>
      {grant ? (
        <>
          <p className="text-ink text-sm">
            Comped to <strong className="font-medium">{grant.plan}</strong> for {grant.services}{" "}
            services until {formatTimestamp(grant.ends_at)} — {grant.reason}. The paid plan
            underneath is still {paidPlan}.
          </p>
          <div>
            <Button
              variant="secondary"
              pending={end.isPending}
              pendingLabel="Ending…"
              onClick={() => end.mutate(undefined)}
            >
              End the comp now
            </Button>
          </div>
        </>
      ) : (
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!endsOn || !reason.trim()) return;
            give.mutate({
              plan,
              services: Number(services),
              ends_on: endsOn,
              reason: reason.trim(),
            });
          }}
        >
          <p className="w-full text-ink-dim text-sm">Paying for {paidPlan}.</p>
          <SelectField
            label="Comp to"
            value={plan}
            options={GRANTABLE.map((id) => ({ id, label: id }))}
            onChange={setPlan}
            optionLabel={(option) => option.label}
          />
          <div className="w-28">
            <TextField label="Services" name="services" value={services} onChange={setServices} />
          </div>
          <div className="w-44">
            <TextField
              label="Until"
              name="ends-on"
              value={endsOn}
              onChange={setEndsOn}
              hint="YYYY-MM-DD"
            />
          </div>
          <div className="min-w-56 flex-1">
            <TextField label="Because" name="grant-reason" value={reason} onChange={setReason} />
          </div>
          <div className="pb-1">
            <Button type="submit" pending={give.isPending} disabled={!endsOn || !reason.trim()}>
              Give the plan
            </Button>
          </div>
        </form>
      )}
      {(give.error ?? end.error) ? (
        <InlineFailure title="The plan did not change" error={give.error ?? end.error} />
      ) : null}
    </section>
  );
}

/** The owner has not set a password yet: send a new link; the old one stops working. */
function OwnerLink({ orgId }: { orgId: string }) {
  const resend = useResendOwnerLink(orgId);

  return (
    <section aria-label="Owner link" className="border-warning-solid border-l-2 px-3 py-2">
      <p className="text-ink text-sm">The owner has not chosen a password yet.</p>
      <div className="mt-2 flex items-center gap-3">
        <Button
          variant="secondary"
          pending={resend.isPending}
          pendingLabel="Sending…"
          onClick={() => resend.mutate(undefined)}
        >
          Send a new link
        </Button>
        {resend.data ? (
          <span role="status" className="text-ink-dim text-sm">
            {resend.data.owner_link_sent
              ? "Sent. The previous link no longer works."
              : "Made, but the email did not go. Check the mail provider."}
          </span>
        ) : null}
      </div>
      {resend.error ? <InlineFailure title="No link was sent" error={resend.error} /> : null}
    </section>
  );
}

/**
 * View the console as this person, read-only, for up to 30 minutes (BR14) —
 * only inside the owner's own grant. A link rather than an automatic tab: a tab
 * opened after an awaited request is a pop-up to the browser.
 *
 * Opening it replaces this browser's session with the support one — same
 * origin, same cookie since ADR-018 — and ending the view signs that session
 * out. The link says so, because being silently signed out mid-task reads as a
 * bug rather than as the design.
 */
function ViewAs({ orgId, member }: { orgId: string; member: OrganizationPage["members"][number] }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const start = useViewAs(orgId);
  const who = member.name ?? member.email;

  if (!open) {
    return (
      <span className="ml-3">
        <Button variant="secondary" onClick={() => setOpen(true)}>
          View as
        </Button>
      </span>
    );
  }
  return (
    <form
      aria-label={`View as ${who}`}
      className="mt-2 flex flex-wrap items-end gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (reason.trim().length >= 3) start.mutate({ user_id: member.id, reason: reason.trim() });
      }}
    >
      <div className="min-w-64 flex-1">
        <TextField
          label="Why"
          name="reason"
          value={reason}
          onChange={setReason}
          hint="The owner reads this in their audit log."
        />
      </div>
      <div className="pb-1">
        {start.data ? (
          <>
            <a
              href={start.data.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-brand-text text-sm underline"
            >
              Open as {who} (works once, within a minute)
            </a>
            <p className="mt-1 text-ink-dim text-xs">
              This signs you out of your own session in this browser until the view ends.
            </p>
          </>
        ) : (
          <Button type="submit" pending={start.isPending} disabled={reason.trim().length < 3}>
            Start a support view
          </Button>
        )}
      </div>
      {start.error ? (
        <div className="w-full">
          <InlineFailure title="No support view was started" error={start.error} />
        </div>
      ) : null}
    </form>
  );
}
