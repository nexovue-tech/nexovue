import { useState } from "react";
import { Button } from "@/shared/components/Button";
import { DataTable } from "@/shared/components/DataTable";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { formatDecimalBytes } from "@/shared/format/bytes";
import { formatMoney } from "@/shared/format/money";
import { formatCount } from "@/shared/format/number";
import { useCan } from "@/shared/identity";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import type { BillingOverview, PlanOption } from "./api";
import {
  PAYMENT_HISTORY_FILE_URL,
  useBillingOverview,
  useCancelSubscription,
  usePaymentHistory,
  useStartCheckout,
} from "./api";
import {
  allowanceFraction,
  CONTACT_FIELD_LABELS,
  cancellationConfirmLabel,
  cancellationLabel,
  chargeAmount,
  chargeDay,
  chargeOutcome,
  chargeTerms,
  checkoutAvailability,
  describeAllowance,
  describeCancellation,
  describeCost,
  describeSubscription,
  HISTORY_IS_NOT_AN_INVOICE,
  initialCommitment,
  monthlyTotalMinorUnits,
  NO_CHARGES_YET,
  PAYMENT_METHOD_IS_THE_PROVIDERS,
  unfinishedCheckoutNotice,
} from "./billing-view";
import { openCheckout, SCRIPT_BLOCKED } from "./checkout-handoff";

/**
 * P1-75. What this organization is on, what pays for it, and where the month
 * stands against the allowance.
 *
 * **Readable by every member, changeable by the owner alone.** A member who
 * cannot see how close the organization is to the ceiling finds out that
 * ingestion stopped by noticing the telemetry went quiet, which is the failure
 * the allowance design exists to prevent. Starting and ending a mandate is the
 * account holder's own act — `MayCommitSpend` — so those two calls answer 403
 * for anybody else and the screen shows their refusal rather than hiding the
 * controls, because a control that vanishes teaches nobody who to ask.
 */
export function BillingScreen() {
  const canSpend = useCan("commit_spend");
  // Null until the customer picks one, so the API answers with whatever this
  // organization already pays in rather than a guess made here.
  const [currency, setCurrency] = useState<string>();
  const overview = useBillingOverview(currency);

  if (overview.isPending) {
    return (
      <SettingsLayout title="Billing">
        <SkeletonRows rows={6} label="Loading billing" />
      </SettingsLayout>
    );
  }
  if (overview.isError) {
    return (
      <SettingsLayout title="Billing">
        <InlineFailure
          title="Billing could not be loaded"
          error={overview.error}
          onRetry={() => void overview.refetch()}
        />
      </SettingsLayout>
    );
  }

  return (
    <SettingsLayout
      title="Billing"
      description="What you are on, what pays for it, and how much of this month's allowance is spent."
    >
      <div className="flex flex-col gap-10">
        <ThisMonth overview={overview.data} />
        <CurrencyChoice overview={overview.data} onChange={setCurrency} />
        <Arrangement overview={overview.data} canSpend={canSpend} />
        {canSpend ? (
          <Upgrade overview={overview.data} currency={overview.data.currency} />
        ) : (
          <p className="max-w-2xl text-ink-dim text-sm">
            Changing the plan or the subscription is the owner's: it commits the organization to a
            recurring charge.
          </p>
        )}
        <Charges overview={overview.data} />
      </div>
    </SettingsLayout>
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
        {/* Monitored, not billed: on a capped tier the two differ, and the row
            that says only "Services" is the one a customer reads as both. */}
        <Row label="Services monitored" value={formatCount(period.services)} />
        <Row
          label="Cost"
          value={describeCost(period, price?.price_per_service_minor_units ?? null)}
        />
      </dl>

      {fraction === null ? null : (
        // A bar so "how close am I" is a proportion rather than two byte counts
        // to divide. Hidden from assistive technology on purpose: the sentence
        // below states both figures and what happens at the limit, so announcing
        // this too would read the same fact twice and less precisely.
        <div className="h-1.5 w-full overflow-hidden rounded bg-raised" aria-hidden="true">
          <div
            className={fraction >= 1 ? "h-full bg-critical-solid" : "h-full bg-brand"}
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

/**
 * Which currency the figures are in, when there is more than one to be in.
 *
 * Hidden entirely at one currency rather than shown disabled: a control with a
 * single option is a question with one answer.
 *
 * **A display choice, not a billing one.** Once a mandate is signed it keeps
 * debiting the currency it was authorised in, so switching here changes what is
 * quoted and never what an existing subscription charges.
 */
function CurrencyChoice({
  overview,
  onChange,
}: {
  overview: BillingOverview;
  onChange: (currency: string) => void;
}) {
  if (overview.available_currencies.length < 2) return null;

  return (
    <section className="flex items-baseline gap-3">
      <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Shown in</span>
      {overview.available_currencies.map((code) => (
        <button
          key={code}
          type="button"
          onClick={() => onChange(code)}
          className={
            code === overview.currency
              ? "text-ink text-sm underline underline-offset-4"
              : "text-ink-dim text-sm hover:text-ink"
          }
        >
          {code}
        </button>
      ))}
    </section>
  );
}

/**
 * What one tier costs and gives, drawn the same way whether it can be chosen.
 *
 * One component rather than two so a customer who cannot buy yet reads exactly
 * the figures they will be offered — a second rendering is a second chance for
 * the two to state different prices.
 */
function PlanTerms({ plan }: { plan: PlanOption }) {
  return (
    <>
      <span className="flex items-baseline justify-between gap-4">
        <span className="text-ink text-sm">{plan.name}</span>
        <span className="text-ink text-sm">
          {formatMoney(plan.price_per_service_minor_units, plan.currency)} per service
        </span>
      </span>
      <span className="mt-1 block text-ink-dim text-xs">
        {plan.included_bytes_per_service === null
          ? "No volume recorded"
          : `${formatDecimalBytes(plan.included_bytes_per_service)} a month for every service`}
        {" · "}
        {plan.raw_retention_days} days raw
      </span>
    </>
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

function Arrangement({ overview, canSpend }: { overview: BillingOverview; canSpend: boolean }) {
  const cancel = useCancelSubscription();
  const [confirming, setConfirming] = useState(false);
  const subscription = overview.subscription;

  if (!subscription) return null;

  return (
    <section className="flex max-w-2xl flex-col gap-4">
      <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Subscription</h2>
      <p className="text-ink text-sm leading-relaxed">{describeSubscription(subscription)}</p>

      {subscription.status === "cancelled" || !canSpend ? null : confirming ? (
        <div className="rounded border border-warning-border bg-warning-subtle p-4 text-sm">
          <p className="text-warning-text">{describeCancellation(subscription)}</p>
          <div className="mt-3 flex gap-2">
            <Button
              pending={cancel.isPending}
              pendingLabel="Cancelling"
              onClick={() => cancel.mutate(undefined, { onSuccess: () => setConfirming(false) })}
            >
              {cancellationConfirmLabel(subscription)}
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Keep it
            </Button>
          </div>
        </div>
      ) : (
        <div>
          <Button variant="secondary" onClick={() => setConfirming(true)}>
            {cancellationLabel(subscription)}
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
 * Changing plan: a button, then the tiers, then a count, then the processor.
 *
 * **No contact fields unless the processor asks for them.** `required_contact_fields`
 * comes back empty from a processor whose own page collects them, and
 * an empty set draws nothing — so the common path is pick a plan, pick a number,
 * go. The email is not asked for at all: the API falls back to the signed-in
 * member's, because a field whose answer the product already holds is a field
 * nobody should retype.
 */
function Upgrade({ overview, currency }: { overview: BillingOverview; currency: string }) {
  const start = useStartCheckout();
  const [choosing, setChoosing] = useState(false);
  const [chosen, setChosen] = useState<PlanOption>();
  const [committed, setCommitted] = useState(String(initialCommitment(overview)));
  // The mutation succeeded and the processor's own script then failed to load
  // or to start. Held apart from `start.error` because it is a different
  // failure with a different remedy, and the customer is shown one line either
  // way rather than a button that silently does nothing.
  const [handoffError, setHandoffError] = useState<Error | undefined>(undefined);
  const [contact, setContact] = useState<Record<string, string>>({});

  const availability = checkoutAvailability(overview);
  const unfinished = unfinishedCheckoutNotice(overview);
  const heading = overview.subscription ? "Change plan" : "Upgrade";

  if (!availability.available) {
    return (
      <section className="flex max-w-2xl flex-col gap-3">
        <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{heading}</h2>
        <p className="text-ink-dim text-sm leading-relaxed">{availability.reason}</p>
        {availability.nextStep === null ? null : (
          <>
            <p className="text-ink text-sm leading-relaxed">{availability.nextStep}</p>
            {/* The tiers stay on screen while the customer decides whether the
                move is worth it. Shown rather than offered: pressing one would
                start the second mandate the API refuses, so none is pressable. */}
            <ul className="flex flex-col gap-3">
              {overview.plans.map((plan) => (
                <li key={plan.slug} className="rounded border border-line px-4 py-3">
                  <PlanTerms plan={plan} />
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    );
  }

  if (!choosing) {
    return (
      <section className="flex max-w-2xl flex-col gap-3">
        <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{heading}</h2>
        {unfinished ? <p className="text-ink text-sm leading-relaxed">{unfinished}</p> : null}
        {/* Says what the code does today, which is bill the commitment. A later
            change moves the bill back to services actually monitored once the
            charge can be raised; this copy changes with it. What it must not do
            meanwhile is imply the number limits services — it does not, and a
            customer reading it as a limit would be wrong. */}
        <p className="text-ink-dim text-sm leading-relaxed">
          You choose how many services to pay for. That number sets your monthly bill and your
          monthly volume — each plan below says how much volume one service buys. We never bill past
          it and never charge overage.
        </p>
        <p className="text-ink-dim text-sm leading-relaxed">
          <strong className="text-ink">It is not a limit on how many services you run.</strong> Send
          from as many as you like; they share the same monthly volume, so a quiet service leaves
          more for a busy one.
        </p>
        <div>
          <Button onClick={() => setChoosing(true)}>{heading}</Button>
        </div>
      </section>
    );
  }

  if (!chosen) {
    return (
      <section className="flex max-w-2xl flex-col gap-4">
        <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{heading}</h2>
        {overview.plans.length === 0 ? (
          <p className="text-ink-dim text-sm leading-relaxed">
            No other plan is priced in {currency} yet.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {overview.plans.map((plan) => (
              <li key={plan.slug}>
                <button
                  type="button"
                  className="w-full rounded border border-line px-4 py-3 text-left hover:border-line-strong"
                  onClick={() => setChosen(plan)}
                >
                  <PlanTerms plan={plan} />
                </button>
              </li>
            ))}
          </ul>
        )}
        <div>
          <Button variant="secondary" onClick={() => setChoosing(false)}>
            Cancel
          </Button>
        </div>
      </section>
    );
  }

  const count = Number(committed);
  const countError =
    !Number.isInteger(count) || count < 1 ? "A whole number of services, one or more." : undefined;
  const fields = overview.checkout?.required_contact_fields ?? [];
  const incomplete = fields.some((field) => !(contact[field] ?? "").trim());

  return (
    <section className="flex max-w-md flex-col gap-4">
      <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {heading} — {chosen.name}
      </h2>

      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          start.mutate(
            {
              planSlug: chosen.slug,
              currency: chosen.currency,
              servicesCommitted: count,
              contactEmail: contact.email,
              contactName: contact.name,
              contactPhone: contact.phone,
            },
            {
              // Used immediately, never stored: this answer is a bearer
              // credential for exactly one mandate. Which of the two shapes it
              // is, is the processor's business and not this screen's.
              onSuccess: (handoff) => {
                openCheckout(handoff, window).catch((failure: unknown) => {
                  setHandoffError(failure instanceof Error ? failure : new Error(SCRIPT_BLOCKED));
                });
              },
            },
          );
        }}
      >
        <TextField
          label="Services to pay for"
          name="services_committed"
          value={committed}
          onChange={setCommitted}
          error={countError}
          hint="Sets the bill and the monthly volume. Not a cap on how many services report."
        />

        {countError ? null : (
          <p className="text-ink text-sm leading-relaxed">
            {formatMoney(monthlyTotalMinorUnits(chosen, count), chosen.currency)} a month —{" "}
            {formatMoney(chosen.price_per_service_minor_units, chosen.currency)} ×{" "}
            {formatCount(count)}.
          </p>
        )}

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

        <div className="flex gap-2">
          <Button
            type="submit"
            pending={start.isPending}
            pendingLabel="Opening checkout"
            disabled={countError !== undefined || incomplete}
          >
            Continue to payment
          </Button>
          <Button variant="secondary" onClick={() => setChosen(undefined)}>
            Back
          </Button>
        </div>
        <p className="text-ink-dim text-xs leading-relaxed">
          You will authorise the mandate with your payment provider. Your card and bank details
          never reach us.
        </p>
        {start.error || handoffError ? (
          <InlineFailure
            title="The checkout could not be started"
            error={start.error ?? handoffError}
          />
        ) : null}
      </form>
    </section>
  );
}

/**
 * What has actually been charged, and where the tax invoice comes from.
 *
 * **Drawn only for an organization that has had an arrangement.** A free-tier
 * account has never been charged, and a permanently empty section is a question
 * nobody asked.
 *
 * **Two sentences the product must say out loud.** A tax invoice is not
 * something this product can produce — it holds no seller registration, no
 * numbered series and no billing address — and the payment method is not
 * something it holds at all. Saying so is the honest answer; drawing a card
 * summary from nothing, or a document that looked like an invoice, would not be.
 */
function Charges({ overview }: { overview: BillingOverview }) {
  const history = usePaymentHistory(overview.subscription !== null);

  if (overview.subscription === null) return null;

  return (
    <section className="flex max-w-2xl flex-col gap-4">
      <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Charges</h2>
      <p className="text-ink-dim text-sm leading-relaxed">{HISTORY_IS_NOT_AN_INVOICE}</p>
      <p className="text-ink-dim text-sm leading-relaxed">{PAYMENT_METHOD_IS_THE_PROVIDERS}</p>

      {history.isPending ? (
        <SkeletonRows rows={3} label="Loading charges" />
      ) : history.isError ? (
        <InlineFailure
          title="Charges could not be loaded"
          error={history.error}
          onRetry={() => void history.refetch()}
        />
      ) : (
        <>
          <DataTable
            caption="Charges recorded against this organization"
            rows={history.data.charges}
            rowKey={(charge) => `${charge.charged_at}-${charge.plan_slug}`}
            empty={NO_CHARGES_YET}
            columns={[
              { key: "date", header: "Date", cell: chargeDay },
              {
                key: "terms",
                header: "What for",
                cell: (charge) => <span className="text-ink-dim">{chargeTerms(charge)}</span>,
              },
              {
                key: "outcome",
                header: "Status",
                cell: (charge) => <span className="text-ink-dim">{chargeOutcome(charge)}</span>,
              },
              { key: "amount", header: "Total", numeric: true, cell: chargeAmount },
            ]}
          />
          <div>
            {/* A link, never a blob this page builds: a page in a sandboxed
                frame cannot hand itself a file, while the browser saves a
                same-origin response that says `attachment`. */}
            <a
              href={PAYMENT_HISTORY_FILE_URL}
              className="text-ink-dim text-sm underline underline-offset-4 hover:text-ink"
            >
              Download as CSV
            </a>
          </div>
        </>
      )}
    </section>
  );
}
