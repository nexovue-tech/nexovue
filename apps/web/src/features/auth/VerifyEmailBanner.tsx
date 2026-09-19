import { useMutation } from "@tanstack/react-query";

import { Button } from "@/shared/components/Button";

import { resendVerificationEmail, useSession } from "./api";

/**
 * The offer to send a fresh verification link, in the signed-in shell.
 *
 * **It is an offer, not a warning, and the tones are load-bearing.** Verification gates
 * nothing in Phase 1 by design — the plan keeps it after signup so the first-span
 * celebration stays intact — so this carries `role="status"`, brand tones rather than
 * warning ones, and no exclamation. A customer who never clicks it loses nothing. The
 * reason it exists at all is that a link can expire or never arrive, and until now there
 * was no way to ask for another.
 *
 * It disappears the moment `email_verified_at` is set, and shows nothing at all while
 * the session is still loading or failed to load: an offer that flickers on every screen
 * change would be worse than no offer.
 */
export function VerifyEmailBanner() {
  const session = useSession();

  const resend = useMutation({
    mutationKey: ["auth", "verify-email", "resend"],
    mutationFn: resendVerificationEmail,
  });

  const user = session.data?.user;
  // A support view is not the person, and may not send them mail.
  if (!user || user.email_verified_at !== null || session.data?.support_view) return null;

  return (
    <p
      role="status"
      // A neutral strip with a brand hairline under it. Filling it with a colour — even a
      // good-news one — would give an errand nobody has to run the weight of the offline
      // banner above it.
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-brand-line border-b bg-raised px-4 py-1.5 text-ink-dim text-xs"
    >
      {resend.isSuccess ? (
        <ResendConfirmed address={user.email} />
      ) : (
        <>
          <span>
            {/* It gates one thing since F21: alerts to the organization's owners go only to
                a verified address, so an address nobody controls is never paged. */}
            <span className="text-ink">{user.email}</span> is not verified yet. Alerts to your
            organization's owners are sent only to a verified address.
          </span>
          {resend.isError ? (
            <span className="text-ink">That did not go through. Try again in a moment.</span>
          ) : null}
          <Button
            variant="secondary"
            pending={resend.isPending}
            pendingLabel="Sending…"
            onClick={() => resend.mutate()}
          >
            Send a new link
          </Button>
        </>
      )}
    </p>
  );
}

/**
 * What a 202 is allowed to say. The API has handed the mail to a provider; it has not
 * watched it arrive, so neither "sent" nor a tick belongs here.
 *
 * The button is gone rather than disabled once this shows. Resend sits on the strict auth
 * tier — ten a minute — and a customer who taps a live button four times while waiting
 * would spend that allowance on mail they are already getting. Reloading the page brings
 * the offer back, which is the right escape hatch for a link that genuinely never came.
 */
function ResendConfirmed({ address }: { address: string }) {
  return (
    <span>
      A new link is on its way to <span className="text-ink">{address}</span>. We have handed it to
      our mail provider, so give it a few minutes — and look in spam before asking for another.
    </span>
  );
}
