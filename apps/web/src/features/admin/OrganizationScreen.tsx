import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
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
    // The same frame as Services and Traces: the identity of what is on screen
    // holds while the detail under it scrolls, and the width is the window's.
    // Capped at `4xl` this left a third of a wide window empty beside two lists
    // that wanted the columns.
    <article aria-label={organization.name} className="flex h-full min-h-0 w-full flex-col">
      <header className="shrink-0 border-line-subtle border-b px-4 pt-5 pb-3">
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

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pt-5 pb-10">
        {/* **The two controls sit side by side from `xl`.** They are one decision
            — what this customer may send, and what they pay for it — and stacked
            they were two short panels with the window's right half empty beside
            each. The lists below stay full width: they are the long things. */}
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start">
          <div className="min-w-0 flex-1">
            <Ingest orgId={orgId} suspension={suspension} />
          </div>
          <div className="min-w-0 flex-1">
            <Plan orgId={orgId} paidPlan={organization.plan} grant={page.data.plan_grant} />
          </div>
        </div>

        {members.some((member) => member.role === "owner" && member.status === "invited") ? (
          <div className="mt-4">
            <OwnerLink orgId={orgId} />
          </div>
        ) : null}

        <Members orgId={orgId} members={members} />
        <Projects projects={projects} />
      </div>
    </article>
  );
}

/** A panel, so every block on this screen has the same box around it. */
function Panel({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section aria-label={label} className="border border-line bg-surface px-4 py-3">
      <h2 className="font-medium font-serif text-base text-ink">{label}</h2>
      <div className="mt-2">{children}</div>
    </section>
  );
}

/**
 * Who is in this organization, in columns.
 *
 * It used to be one run-on sentence per member — name, address, role and status
 * separated by interpuncts — which is unreadable past three people and gives the
 * "View as" button nowhere fixed to sit. Shared widths, like the customer list.
 */
function Members({ orgId, members }: { orgId: string; members: OrganizationPage["members"] }) {
  return (
    <section aria-label="Members" className="mt-6">
      <h2 className="font-medium font-serif text-base text-ink">Members</h2>
      <div className="mt-2 flex items-center gap-3 border-line border-b px-1 pb-1 font-mono text-2xs text-ink-dim uppercase tracking-caps">
        <span className="min-w-0 flex-1">Member</span>
        <span className="hidden w-64 shrink-0 md:block">Email</span>
        <span className="w-24 shrink-0">Role</span>
        <span className="w-20 shrink-0">Status</span>
        <span className="w-44 shrink-0 text-right">Support</span>
      </div>
      <ul>
        {members.map((member) => (
          <MemberRow key={member.id} orgId={orgId} member={member} />
        ))}
      </ul>
    </section>
  );
}

/**
 * One member, and the support view they can be seen as.
 *
 * The row owns whether the view is being started, because the form does not fit
 * the column the button sits in: the button is a cell, the form is a block under
 * the whole row. Packed into the cell it wrapped into four lines and pushed every
 * row below it out of its columns.
 */
function MemberRow({
  orgId,
  member,
}: {
  orgId: string;
  member: OrganizationPage["members"][number];
}) {
  const [starting, setStarting] = useState(false);

  return (
    <li className="border-line-subtle border-b py-2 text-sm">
      <div className="flex items-center gap-3">
        <span className="min-w-0 flex-1 truncate text-ink">{member.name ?? member.email}</span>
        <span className="hidden w-64 shrink-0 truncate text-ink-dim md:block">{member.email}</span>
        <span className="w-24 shrink-0 text-ink-dim">{member.role}</span>
        <span className="w-20 shrink-0 text-ink-dim">
          {member.status === "invited" ? "invited" : "active"}
        </span>
        <span className="w-44 shrink-0 text-right">
          {member.status === "active" && !starting ? (
            <Button variant="secondary" onClick={() => setStarting(true)}>
              View as
            </Button>
          ) : null}
        </span>
      </div>

      {starting ? (
        <ViewAs orgId={orgId} member={member} onCancel={() => setStarting(false)} />
      ) : null}
    </li>
  );
}

function Projects({ projects }: { projects: OrganizationPage["projects"] }) {
  return (
    <section aria-label="Projects" className="mt-6">
      <h2 className="font-medium font-serif text-base text-ink">Projects</h2>
      <div className="mt-2 flex items-center gap-3 border-line border-b px-1 pb-1 font-mono text-2xs text-ink-dim uppercase tracking-caps">
        <span className="min-w-0 flex-1">Project</span>
        <span className="hidden w-48 shrink-0 sm:block">Slug</span>
        <span className="w-32 shrink-0 text-right">Last data</span>
      </div>
      {projects.length === 0 ? (
        <p className="py-2 text-ink-dim text-sm">No projects yet.</p>
      ) : (
        <ul>
          {projects.map((project) => (
            <li
              key={project.id}
              className="flex items-center gap-3 border-line-subtle border-b py-2 text-sm"
            >
              <span className="min-w-0 flex-1 truncate text-ink">{project.name}</span>
              <span className="hidden w-48 shrink-0 truncate font-mono text-ink-dim text-xs sm:block">
                {project.slug}
              </span>
              {/* A missing reading is an em dash, never a zero or a blank. */}
              <span className="w-32 shrink-0 text-right text-ink-dim">
                {project.last_ingest_day ?? "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
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
    <Panel label="Ingest">
      {suspension ? (
        <div className="flex flex-col gap-3">
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
        </div>
      ) : (
        <>
          <p className="text-ink-dim text-sm">Accepting data.</p>
          {/* **Aligned on the controls, not on the tallest box.** The hint is a
              line under the form rather than inside the field, so the input and
              the button are the same height and line up by construction — the
              `pb-1` shim this used to carry was guessing at the hint's height,
              and moved again whenever the hint wrapped. */}
          <form
            className="mt-3 flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (reason.trim()) suspend.mutate(reason.trim());
            }}
          >
            <div className="min-w-56 flex-1">
              <TextField
                label="Suspend, because"
                name="reason"
                value={reason}
                onChange={setReason}
              />
            </div>
            <div>
              <Button type="submit" pending={suspend.isPending} disabled={!reason.trim()}>
                Suspend ingest
              </Button>
            </div>
          </form>
          <p className="mt-1.5 text-ink-dim text-xs">
            The customer will ask why; this is what they are told.
          </p>
        </>
      )}
      {(suspend.error ?? resume.error) ? (
        <InlineFailure title="The valve did not move" error={suspend.error ?? resume.error} />
      ) : null}
    </Panel>
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
    <Panel label="Plan">
      {grant ? (
        <div className="flex flex-col gap-3">
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
        </div>
      ) : (
        <>
          <p className="text-ink-dim text-sm">Paying for {paidPlan}.</p>
          {/* Four controls of one height. `stacked` is what makes the select one
              of them: inline it is 26px against the inputs' 34px, which is the
              misalignment the `pb-1` shim was there to hide. */}
          <form
            className="mt-3 flex flex-wrap items-end gap-3"
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
            <div className="w-28">
              <SelectField
                stacked
                label="Comp to"
                value={plan}
                options={GRANTABLE.map((id) => ({ id, label: id }))}
                onChange={setPlan}
                optionLabel={(option) => option.label}
              />
            </div>
            <div className="w-24">
              <TextField label="Services" name="services" value={services} onChange={setServices} />
            </div>
            <div className="w-40">
              <TextField label="Until" name="ends-on" value={endsOn} onChange={setEndsOn} />
            </div>
            <div className="min-w-48 flex-1">
              <TextField label="Because" name="grant-reason" value={reason} onChange={setReason} />
            </div>
            <div>
              <Button type="submit" pending={give.isPending} disabled={!endsOn || !reason.trim()}>
                Give the plan
              </Button>
            </div>
          </form>
          <p className="mt-1.5 text-ink-dim text-xs">
            Until is a date, as YYYY-MM-DD. The reason is for our own record, not the customer's.
          </p>
        </>
      )}
      {(give.error ?? end.error) ? (
        <InlineFailure title="The plan did not change" error={give.error ?? end.error} />
      ) : null}
    </Panel>
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
function ViewAs({
  orgId,
  member,
  onCancel,
}: {
  orgId: string;
  member: OrganizationPage["members"][number];
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const start = useViewAs(orgId);
  const who = member.name ?? member.email;

  if (start.data) {
    return (
      <div className="mt-3 border-brand-line border-l-2 bg-brand-surface px-3 py-2">
        <a
          href={start.data.url}
          target="_blank"
          rel="noopener noreferrer"
          className="font-medium text-brand-text text-sm underline"
        >
          Open as {who} — works once, within a minute
        </a>
        <p className="mt-1 max-w-3xl text-ink-dim text-xs leading-relaxed">
          This signs you out of your own session in this browser until the view ends.
        </p>
      </div>
    );
  }

  return (
    <>
      <form
        aria-label={`View as ${who}`}
        className="mt-3 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (reason.trim().length >= 3)
            start.mutate({ user_id: member.id, reason: reason.trim() });
        }}
      >
        <div className="min-w-56 flex-1">
          <TextField label="Why" name="reason" value={reason} onChange={setReason} />
        </div>
        <div>
          <Button type="submit" pending={start.isPending} disabled={reason.trim().length < 3}>
            Start a support view
          </Button>
        </div>
        {/* A way back out. Opened by mistake, the row otherwise keeps a form on it
            for as long as the page is open. */}
        <div>
          <Button variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
      <p className="mt-1.5 text-ink-dim text-xs">{who} reads this in their audit log.</p>
      {start.error ? (
        <InlineFailure title="No support view was started" error={start.error} />
      ) : null}
    </>
  );
}
