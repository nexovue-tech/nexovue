import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { formatDecimalBytes } from "@/shared/format/bytes";
import { formatMoney } from "@/shared/format/money";
import { formatCount } from "@/shared/format/number";

import type { BillingOverview } from "./api";
import { useBillingOverview, useCancelSubscription, useStartCheckout } from "./api";
import {
  allowanceFraction,
  CONTACT_FIELD_LABELS,
  checkoutAvailability,
  describeAllowance,
  describeCost,
  describeSubscription,
  unfinishedCheckoutNotice,
} from "./billing-view";

/**
 * P1-75. What this organization is on, what pays for it, and where the month
 * stands against the allowance.
 *
 * **Readable by every member, changeable by the owner alone.** A member who
 * cannot see how close the organization is to the ceiling finds out that
 * ingestion stopped by noticing the telemetry went quiet, which is the failure
 * ADR 0107 exists to prevent. Starting and ending a mandate is the account
 * holder's own act — `MayCommitSpend` — so those two calls answer 403 for
 * anybody else and the screen shows their refusal rather than hiding the
 * controls, because a control that vanishes teaches nobody who to ask.
 */
export function BillingScreen() {
  const overview = useBillingOverview();

  if (overview.isPending) return <SkeletonRows rows={6} label="Loading billing" />;
  if (overview.isError) {
    return (
      <div className="px-6 py-8">
        <InlineFailure
          title="Billing could not be loaded"
          error={overview.error}
          onRetry={() => void overview.refetch()}
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-10 px-6 py-8">
      <header className="max-w-2xl">
        <h1 className="font-medium font-serif text-ink text-xl tracking-tight">Billing</h1>
        <p className="mt-2 text-ink-dim text-sm leading-relaxed">
          What you are on, what pays for it, and how much of this month&rsquo;s allowance is spent.
        </p>
      </header>

      <ThisMonth overview={overview.data} />
      <Arrangement overview={overview.data} />
      <Upgrade overview={overview.data} />
    </div>
  );
}

function ThisMonth({ overview }: { overview: BillingOverview }) {
  const { period, plan, price } = overview;
  const fraction = allowanceFraction(period);

  return (
    <section className="flex max-w-2xl flex-col gap-4">
      <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">This month</h2>

      <dl className="flex flex-col gap-3 text-sm">
        <Row label="Plan" value={plan.name} />
        <Row
          label="Rate"
          value={
            price
              ? `${formatMoney(price.price_per_service_minor_units, price.currency)} per service`
              : "Priced by agreement"
          }
        />
        <Row label="Services" value={formatCount(period.services)} />
        <Row label="Cost" value={describeCost(period)} />
      </dl>

      {fraction === null ? null : (
        // A bar so "how close am I" is a proportion rather than two byte counts
        // to divide. Hidden from assistive technology on purpose: the sentence
        // below states both figures and what happens at the limit, so announcing
        // this too would read the same fact twice and less precisely.
        <div className="h-1.5 w-full overflow-hidden rounded bg-surface-raised" aria-hidden="true">
          <div
            className={fraction >= 1 ? "h-full bg-danger" : "h-full bg-brand"}
            style={{ width: `${Math.min(100, Math.round(fraction * 100))}%` }}
          />
        </div>
      )}
      <p className="text-ink-dim text-sm leading-relaxed">{describeAllowance(period)}</p>
      {period.browser_included_bytes === null ? null : (
        <p className="text-ink-dim text-sm leading-relaxed">
          Of that, at most {formatDecimalBytes(period.browser_included_bytes)} may come from the
          browser. {formatDecimalBytes(period.browser_bytes_ingested)} has so far, and anything it
          does not use stays available to your servers.
        </p>
      )}
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-4">
      <dt className="w-32 shrink-0 text-ink-dim">{label}</dt>
      <dd className="text-ink">{value}</dd>
    </div>
  );
}

function Arrangement({ overview }: { overview: BillingOverview }) {
  const cancel = useCancelSubscription();
  const [confirming, setConfirming] = useState(false);
  const subscription = overview.subscription;

  if (!subscription) return null;

  return (
    <section className="flex max-w-2xl flex-col gap-4">
      <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Subscription</h2>
      <p className="text-ink text-sm leading-relaxed">{describeSubscription(subscription)}</p>

      {subscription.status === "cancelled" ? null : confirming ? (
        <div className="rounded border border-warning-border bg-warning-subtle p-4 text-sm">
          <p className="text-warning-text">
            Cancelling returns this organization to the free tier when the processor&rsquo;s current
            period ends. The free tier&rsquo;s allowance is smaller, and ingestion stops when it is
            reached.
          </p>
          <div className="mt-3 flex gap-2">
            <Button
              pending={cancel.isPending}
              pendingLabel="Cancelling"
              onClick={() => cancel.mutate(undefined, { onSuccess: () => setConfirming(false) })}
            >
              Yes, cancel the subscription
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Keep it
            </Button>
          </div>
        </div>
      ) : (
        <div>
          <Button variant="secondary" onClick={() => setConfirming(true)}>
            Cancel subscription
          </Button>
        </div>
      )}
      {cancel.error ? (
        <InlineFailure title="The subscription was not cancelled" error={cancel.error} />
      ) : null}
    </section>
  );
}

/**
 * The upgrade form, drawn only when a checkout could actually start.
 *
 * The fields come from `overview.checkout.required_contact_fields` — the
 * processor declares what it needs (ADR 0109), so switching processors deletes
 * a field here rather than leaving a dead one.
 */
function Upgrade({ overview }: { overview: BillingOverview }) {
  const start = useStartCheckout();
  const [committed, setCommitted] = useState("1");
  const [contact, setContact] = useState<Record<string, string>>({});

  const availability = checkoutAvailability(overview);
  if (!availability.available) {
    return (
      <section className="flex max-w-2xl flex-col gap-2">
        <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Upgrade</h2>
        <p className="text-ink-dim text-sm leading-relaxed">{availability.reason}</p>
      </section>
    );
  }

  const unfinished = unfinishedCheckoutNotice(overview);
  const fields = overview.checkout?.required_contact_fields ?? [];
  const count = Number(committed);
  const countError =
    !Number.isInteger(count) || count < 1 ? "A whole number of services, one or more." : undefined;
  const incomplete = fields.some((field) => !(contact[field] ?? "").trim());

  return (
    <section className="flex max-w-md flex-col gap-4">
      <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Upgrade</h2>
      {unfinished ? <p className="text-ink text-sm leading-relaxed">{unfinished}</p> : null}
      <p className="text-ink-dim text-sm leading-relaxed">
        You choose the ceiling. It is the most you can be charged in a month and the volume you may
        send, and you can raise it later by signing a new mandate. We never bill past it and never
        charge overage.
      </p>

      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          start.mutate(
            {
              planSlug: "pro",
              servicesCommitted: count,
              contactEmail: contact.email ?? "",
              contactName: contact.name,
              contactPhone: contact.phone,
            },
            {
              // Navigated to, never stored: the URL is a bearer credential for
              // exactly one mandate.
              onSuccess: (handoff) => {
                window.location.href = handoff.url;
              },
            },
          );
        }}
      >
        <TextField
          label="Services to commit to"
          name="services_committed"
          value={committed}
          onChange={setCommitted}
          error={countError}
          hint="Your ceiling. Both what you can be charged and what you can send."
        />

        {fields.map((field) => {
          const spec = CONTACT_FIELD_LABELS[field];
          return (
            <TextField
              key={field}
              label={spec?.label ?? field}
              name={field}
              value={contact[field] ?? ""}
              onChange={(value) => setContact({ ...contact, [field]: value })}
              autoComplete={spec?.autoComplete}
            />
          );
        })}

        <div>
          <Button
            type="submit"
            pending={start.isPending}
            pendingLabel="Opening checkout"
            disabled={countError !== undefined || incomplete}
          >
            Continue to payment
          </Button>
        </div>
        <p className="text-ink-dim text-xs leading-relaxed">
          You will authorise the mandate on your payment provider&rsquo;s own page. Your card and
          bank details never reach us.
        </p>
        {start.error ? (
          <InlineFailure title="The checkout could not be started" error={start.error} />
        ) : null}
      </form>
    </section>
  );
}
