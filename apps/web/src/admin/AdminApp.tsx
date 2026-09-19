import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";

import {
  adminRequest,
  isSignedOut,
  type OrganizationPage,
  type OrganizationRow,
  type Staff,
} from "./api";

/**
 * nexovue's staff console (F31, ADR-016). Its own build entry, importing only `shared/`:
 * nothing here can reach a customer screen, and no customer screen can reach this.
 *
 * Three places, addressed by the hash so a link to an organization can be shared among
 * staff: sign-in, the search (`#/`), and one organization (`#/org/<id>`).
 */

const ME_KEY = ["admin", "me"] as const;

function useHash(): string {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export function AdminApp() {
  const me = useQuery({
    queryKey: ME_KEY,
    queryFn: () => adminRequest<Staff>("GET", "me"),
    retry: false,
  });
  const hash = useHash();

  if (me.isPending) {
    return (
      <Frame>
        <SkeletonRows rows={2} label="Loading" />
      </Frame>
    );
  }
  if (me.isError) {
    if (isSignedOut(me.error)) return <SignIn />;
    return (
      <Frame>
        <InlineFailure title="The admin API could not be reached" error={me.error} />
      </Frame>
    );
  }

  const organization = hash.match(/^#\/org\/([0-9a-f-]{36})$/)?.[1];
  return (
    <Frame staff={me.data}>
      {organization ? (
        <OrganizationScreen orgId={organization} />
      ) : hash === "#/onboard" ? (
        <OnboardScreen />
      ) : (
        <OrganizationsScreen />
      )}
    </Frame>
  );
}

function Frame({ staff, children }: { staff?: Staff; children: React.ReactNode }) {
  const client = useQueryClient();
  const signOut = useMutation({
    mutationFn: () => adminRequest<void>("POST", "sign-out"),
    onSuccess: () => client.invalidateQueries({ queryKey: ME_KEY }),
  });
  return (
    <div className="min-h-screen bg-page text-ink">
      <header className="flex items-center justify-between border-line border-b px-4 py-2">
        <a href="#/" className="font-medium font-serif text-ink">
          nexovue <span className="text-warning-text">staff</span>
        </a>
        {staff ? (
          <span className="flex items-center gap-3 text-ink-dim text-sm">
            {staff.name}
            <Button variant="secondary" onClick={() => signOut.mutate()}>
              Sign out
            </Button>
          </span>
        ) : null}
      </header>
      <main className="mx-auto max-w-4xl px-4 py-6">{children}</main>
    </div>
  );
}

function SignIn() {
  const client = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const signIn = useMutation({
    mutationFn: () => adminRequest<Staff>("POST", "sign-in", { email, password, code }),
    onSuccess: (staff) => client.setQueryData(ME_KEY, staff),
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    signIn.mutate();
  }

  return (
    <Frame>
      <h1 className="font-semibold font-serif text-xl">Staff sign-in</h1>
      <p className="mt-1 text-ink-dim text-sm">
        Address, password and the code from your authenticator — all three, every time.
      </p>
      <form onSubmit={onSubmit} className="mt-6 flex max-w-sm flex-col gap-4" noValidate>
        <TextField label="Email" name="email" type="email" value={email} onChange={setEmail} />
        <TextField
          label="Password"
          name="password"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete="current-password"
        />
        <TextField
          label="Authenticator code"
          name="code"
          value={code}
          onChange={setCode}
          autoComplete="one-time-code"
        />
        <div>
          <Button type="submit" pending={signIn.isPending} pendingLabel="Signing in…">
            Sign in
          </Button>
        </div>
        {signIn.error ? <InlineFailure title="Not signed in" error={signIn.error} /> : null}
      </form>
    </Frame>
  );
}

function OrganizationsScreen() {
  const [typed, setTyped] = useState("");
  const [needle, setNeedle] = useState("");
  const results = useQuery({
    queryKey: ["admin", "organizations", needle],
    queryFn: () =>
      adminRequest<{ organizations: OrganizationRow[] }>(
        "GET",
        `organizations?q=${encodeURIComponent(needle)}`,
      ),
    enabled: needle.length >= 2,
  });

  return (
    <section aria-label="Organizations">
      <div className="flex items-baseline justify-between gap-3">
        <h1 className="font-semibold font-serif text-xl">Organizations</h1>
        <a href="#/onboard" className="font-medium text-brand-text text-sm hover:underline">
          Onboard a paying customer
        </a>
      </div>
      <form
        className="mt-4 flex max-w-lg items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          setNeedle(typed.trim());
        }}
      >
        <div className="flex-1">
          <TextField
            label="Find by name, slug, id or a member's email"
            name="needle"
            value={typed}
            onChange={setTyped}
          />
        </div>
        <div className="pb-1">
          <Button type="submit">Search</Button>
        </div>
      </form>
      <div className="mt-6">
        {needle.length < 2 ? null : results.isPending ? (
          <SkeletonRows rows={3} label="Searching" />
        ) : results.isError ? (
          <InlineFailure title="The search failed" error={results.error} />
        ) : results.data.organizations.length === 0 ? (
          <EmptyState title="No organization matches" detail={`Nothing matches “${needle}”.`} />
        ) : (
          <ul className="border-line border-t" aria-label="Results">
            {results.data.organizations.map((row) => (
              <li key={row.id} className="border-line-subtle border-b py-2.5">
                <a href={`#/org/${row.id}`} className="font-medium text-ink hover:underline">
                  {row.name}
                </a>{" "}
                <span className="font-mono text-ink-dim text-xs">{row.slug}</span>
                <p className="text-ink-dim text-sm">
                  {row.plan} · {row.region} · created {formatRelativeTime(row.created_at)}
                  {row.suspended ? " · ingest suspended" : ""}
                  {row.deleted ? " · deleted" : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function OrganizationScreen({ orgId }: { orgId: string }) {
  const page = useQuery({
    queryKey: ["admin", "organization", orgId],
    queryFn: () => adminRequest<OrganizationPage>("GET", `organizations/${orgId}`),
  });
  if (page.isPending) return <SkeletonRows rows={6} label="Loading the organization" />;
  if (page.isError) {
    return <InlineFailure title="The organization could not be loaded" error={page.error} />;
  }
  const { organization, suspension, members, projects, usage } = page.data;
  return (
    <article aria-label={organization.name} className="flex flex-col gap-8">
      <header>
        <a href="#/" className="text-ink-dim text-sm hover:text-ink">
          All organizations
        </a>
        <h1 className="mt-2 font-semibold font-serif text-xl">{organization.name}</h1>
        <p className="text-ink-dim text-sm">
          <span className="font-mono">{organization.slug}</span> · {organization.region} · plan{" "}
          {usage?.plan.name ?? organization.plan} · created{" "}
          {formatTimestamp(organization.created_at)}
        </p>
      </header>

      <Ingest orgId={organization.id} suspension={suspension} />
      <Plan orgId={organization.id} paidPlan={organization.plan} grant={page.data.plan_grant} />

      {members.some((member) => member.role === "owner" && member.status === "invited") ? (
        <OwnerLink orgId={organization.id} />
      ) : null}

      <section aria-label="Members">
        <h2 className="font-medium font-serif text-base">Members</h2>
        <ul className="mt-2 border-line border-t">
          {members.map((member) => (
            <li key={member.id} className="border-line-subtle border-b py-2 text-sm">
              {member.name ?? member.email}{" "}
              <span className="text-ink-dim">
                {member.email} · {member.role}
                {member.status === "invited" ? " · invited" : ""}
              </span>
              {member.status === "active" ? (
                <ViewAs orgId={organization.id} member={member} />
              ) : null}
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Projects">
        <h2 className="font-medium font-serif text-base">Projects</h2>
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

/**
 * View the console as this person, read-only, for up to 30 minutes (F31, BR14) — only
 * inside the owner's grant. The link works once, for 60 seconds; it is a link rather
 * than an automatic new tab so the browser does not block it as a pop-up.
 */
function ViewAs({ orgId, member }: { orgId: string; member: OrganizationPage["members"][number] }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const start = useMutation({
    mutationFn: (why: string) =>
      adminRequest<{ url: string }>("POST", `organizations/${orgId}/view-as`, {
        user_id: member.id,
        reason: why,
      }),
  });
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
        if (reason.trim().length >= 3) start.mutate(reason.trim());
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
          <a
            href={start.data.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-brand-text text-sm underline"
          >
            Open as {who} (works once, within a minute)
          </a>
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

/** The owner has not set a password yet: send a new link; the old one stops working. */
function OwnerLink({ orgId }: { orgId: string }) {
  const resend = useMutation({
    mutationFn: () =>
      adminRequest<{ owner_link_sent: boolean }>("POST", `organizations/${orgId}/owner-link`),
  });
  return (
    <section aria-label="Owner link" className="border-warning-solid border-l-2 px-3 py-2">
      <p className="text-ink text-sm">The owner has not chosen a password yet.</p>
      <div className="mt-2 flex items-center gap-3">
        <Button variant="secondary" pending={resend.isPending} onClick={() => resend.mutate()}>
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

const REGIONS = [
  { id: "india", label: "India (Mumbai)" },
  { id: "india-south", label: "India South (Hyderabad)" },
] as const;

/**
 * After a customer pays on the website (ADR-008): their organization, their owner, and
 * the payment that bought it, in one step. The owner is emailed a link to choose their
 * own password — staff never set or see it.
 */
function OnboardScreen() {
  const [organizationName, setOrganizationName] = useState("");
  const [ownerEmail, setOwnerEmail] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [region, setRegion] = useState<string>("india");
  const [paymentId, setPaymentId] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("INR");
  const onboard = useMutation({
    mutationFn: () =>
      adminRequest<{ organization_id: string; owner_link_sent: boolean }>("POST", "organizations", {
        organization_name: organizationName,
        owner_email: ownerEmail,
        owner_name: ownerName.trim() || null,
        region,
        payment: {
          razorpay_payment_id: paymentId.trim(),
          amount_minor: Math.round(Number(amount) * 100),
          currency: currency.trim().toUpperCase(),
        },
      }),
    onSuccess: (done) => {
      window.location.hash = `#/org/${done.organization_id}`;
    },
  });

  return (
    <section aria-label="Onboard" className="max-w-lg">
      <a href="#/" className="text-ink-dim text-sm hover:text-ink">
        All organizations
      </a>
      <h1 className="mt-2 font-semibold font-serif text-xl">Onboard a paying customer</h1>
      <form
        className="mt-6 flex flex-col gap-4"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          onboard.mutate();
        }}
      >
        <TextField
          label="Organization name"
          name="organization"
          value={organizationName}
          onChange={setOrganizationName}
        />
        <TextField
          label="Owner's email"
          name="owner-email"
          type="email"
          value={ownerEmail}
          onChange={setOwnerEmail}
        />
        <TextField
          label="Owner's name (optional)"
          name="owner-name"
          value={ownerName}
          onChange={setOwnerName}
        />
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Region</span>
          <select
            value={region}
            onChange={(event) => setRegion(event.target.value)}
            className="border border-line bg-surface px-2 py-1 text-ink"
          >
            {REGIONS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <TextField
          label="Razorpay payment id"
          name="payment-id"
          value={paymentId}
          onChange={setPaymentId}
          hint="From the website payment, e.g. pay_…"
        />
        <div className="flex gap-3">
          <div className="flex-1">
            <TextField
              label="Amount paid"
              name="amount"
              value={amount}
              onChange={setAmount}
              hint="In rupees or the currency below, e.g. 499"
            />
          </div>
          <div className="w-24">
            <TextField label="Currency" name="currency" value={currency} onChange={setCurrency} />
          </div>
        </div>
        <div>
          <Button type="submit" pending={onboard.isPending} pendingLabel="Onboarding…">
            Onboard and email the owner
          </Button>
        </div>
        {onboard.error ? <InlineFailure title="Nothing was created" error={onboard.error} /> : null}
      </form>
    </section>
  );
}

function useOrganizationAction<Input>(orgId: string, run: (input: Input) => Promise<unknown>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin", "organization", orgId] }),
  });
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
  const suspend = useOrganizationAction(orgId, (why: string) =>
    adminRequest<void>("POST", `organizations/${orgId}/suspension`, { reason: why }),
  );
  const resume = useOrganizationAction(orgId, () =>
    adminRequest<void>("DELETE", `organizations/${orgId}/suspension`),
  );
  return (
    <section aria-label="Ingest" className="flex flex-col gap-2">
      <h2 className="font-medium font-serif text-base">Ingest</h2>
      {suspension ? (
        <>
          <p className="text-critical-text text-sm">
            Suspended {formatRelativeTime(suspension.at)}: {suspension.reason}
          </p>
          <div>
            <Button
              variant="secondary"
              pending={resume.isPending}
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

const GRANTABLE = ["pro", "scale"] as const;

/** A complimentary plan until a date (FR27): in force until then, then the paid plan again. */
function Plan({
  orgId,
  paidPlan,
  grant,
}: {
  orgId: string;
  paidPlan: string;
  grant: OrganizationPage["plan_grant"];
}) {
  const [plan, setPlan] = useState<string>("pro");
  const [services, setServices] = useState("3");
  const [endsOn, setEndsOn] = useState("");
  const [reason, setReason] = useState("");
  const give = useOrganizationAction(orgId, () =>
    adminRequest<void>("POST", `organizations/${orgId}/plan-grant`, {
      plan,
      services: Number(services),
      ends_on: endsOn,
      reason,
    }),
  );
  const end = useOrganizationAction(orgId, () =>
    adminRequest<void>("DELETE", `organizations/${orgId}/plan-grant`),
  );
  return (
    <section aria-label="Plan" className="flex flex-col gap-2">
      <h2 className="font-medium font-serif text-base">Plan</h2>
      <p className="text-ink-dim text-sm">
        Paid plan: {paidPlan}.
        {grant
          ? ` Complimentary ${grant.plan} for ${grant.services} services until ${formatTimestamp(grant.ends_at)} — ${grant.reason}.`
          : " No complimentary plan."}
      </p>
      {grant ? (
        <div>
          <Button variant="secondary" pending={end.isPending} onClick={() => end.mutate(undefined)}>
            End the complimentary plan now
          </Button>
        </div>
      ) : null}
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          give.mutate(undefined);
        }}
      >
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Plan</span>
          <select
            value={plan}
            onChange={(event) => setPlan(event.target.value)}
            className="border border-line bg-surface px-2 py-1 text-ink"
          >
            {GRANTABLE.map((slug) => (
              <option key={slug} value={slug}>
                {slug}
              </option>
            ))}
          </select>
        </label>
        <div className="w-24">
          <TextField label="Services" name="services" value={services} onChange={setServices} />
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Until</span>
          <input
            type="date"
            value={endsOn}
            onChange={(event) => setEndsOn(event.target.value)}
            className="border border-line bg-surface px-2 py-1 text-ink"
          />
        </label>
        <div className="min-w-48 flex-1">
          <TextField label="Why" name="grant-reason" value={reason} onChange={setReason} />
        </div>
        <div className="pb-1">
          <Button type="submit" pending={give.isPending} disabled={!endsOn || !reason.trim()}>
            {grant ? "Replace it" : "Give it"}
          </Button>
        </div>
      </form>
      {(give.error ?? end.error) ? (
        <InlineFailure title="The plan did not change" error={give.error ?? end.error} />
      ) : null}
    </section>
  );
}
