import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import { TextField } from "@/shared/components/TextField";
import { useIsOperator } from "@/shared/identity";

import { needsReAuthentication, useOnboard } from "./api";
import { ConfirmItIsYou } from "./ConfirmItIsYou";
import { NotTheOperator } from "./NotTheOperator";

/**
 * After a customer pays on the website (ADR-008): their organization, their
 * owner, and the payment that bought it, in one step. The owner is emailed a
 * link to choose their own password — nexovue never sets or sees one.
 */

const REGIONS = [
  { id: "india", label: "India (Mumbai)" },
  { id: "india-south", label: "India South (Hyderabad)" },
] as const;

const MINOR_UNITS = 100;

export function OnboardScreen() {
  const operator = useIsOperator();
  const navigate = useNavigate();
  const [organizationName, setOrganizationName] = useState("");
  const [ownerEmail, setOwnerEmail] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [region, setRegion] = useState<string>(REGIONS[0].id);
  const [paymentId, setPaymentId] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("INR");
  const onboard = useOnboard();

  if (!operator) return <NotTheOperator />;
  if (needsReAuthentication(onboard.error)) {
    return <ConfirmItIsYou onConfirmed={() => onboard.reset()} />;
  }

  const ready = organizationName.trim() && ownerEmail.trim() && paymentId.trim() && amount.trim();

  return (
    <section aria-label="Onboard" className="w-full max-w-lg px-4 py-5">
      <Link to="/admin" className="text-ink-dim text-sm hover:text-ink">
        All organizations
      </Link>
      <h1 className="mt-2 font-semibold font-serif text-ink text-xl tracking-tight">
        Onboard a paying customer
      </h1>
      <p className="mt-1 text-ink-dim text-sm leading-relaxed">
        The payment is recorded once, against this organization, and cannot be entered twice.
      </p>

      <form
        className="mt-6 flex flex-col gap-4"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          if (!ready) return;
          onboard.mutate(
            {
              organization_name: organizationName.trim(),
              owner_email: ownerEmail.trim(),
              owner_name: ownerName.trim() || null,
              region,
              payment: {
                razorpay_payment_id: paymentId.trim(),
                amount_minor: Math.round(Number(amount) * MINOR_UNITS),
                currency: currency.trim().toUpperCase(),
              },
            },
            {
              onSuccess: (done) =>
                navigate({
                  to: "/admin/organizations/$orgId",
                  params: { orgId: done.organization_id },
                }),
            },
          );
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
          hint="Where the set-your-password link goes."
        />
        <TextField
          label="Owner's name (optional)"
          name="owner-name"
          value={ownerName}
          onChange={setOwnerName}
        />
        <SelectField
          stacked
          label="Region"
          value={region}
          options={REGIONS}
          onChange={setRegion}
          optionLabel={(option) => option.label}
        />
        <TextField
          label="Razorpay payment id"
          name="payment-id"
          value={paymentId}
          onChange={setPaymentId}
        />
        <div className="flex gap-3">
          <div className="flex-1">
            <TextField
              label="Amount"
              name="amount"
              value={amount}
              onChange={setAmount}
              hint="In whole currency — 4999 for ₹4,999."
            />
          </div>
          <div className="w-24">
            <TextField label="Currency" name="currency" value={currency} onChange={setCurrency} />
          </div>
        </div>
        <div>
          <Button
            type="submit"
            pending={onboard.isPending}
            pendingLabel="Onboarding…"
            disabled={!ready}
          >
            Onboard
          </Button>
        </div>
      </form>

      {onboard.error ? <InlineFailure title="Nothing was created" error={onboard.error} /> : null}
    </section>
  );
}
