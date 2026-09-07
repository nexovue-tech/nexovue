import { useMutation } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef } from "react";

import { AuthScreenLayout } from "./AuthScreenLayout";
import { verifyEmail } from "./api";
import { describeLinkRefusal } from "./problem-fields";

/**
 * Where a verification link lands. No form: the customer already clicked the thing, so
 * the screen spends the token on mount and reports what happened.
 *
 * Verification gates nothing in Phase 1 by design — it records that an address is real,
 * it does not unlock a door — so none of the four answers below is written as an
 * emergency.
 */
export function VerifyEmailScreen({ token }: { token?: string }) {
  const verify = useMutation({
    mutationKey: ["auth", "verify-email"],
    mutationFn: verifyEmail,
  });
  const { mutate } = verify;

  // The token is single use, and StrictMode runs this effect twice in development: a
  // second POST would be refused for spending a token the first one already spent. The
  // ref is deliberately never cleared, so one token is submitted at most once per mount.
  const submitted = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (token === undefined || submitted.current === token) return;
    submitted.current = token;
    mutate(token);
  }, [token, mutate]);

  if (token === undefined) {
    return (
      <AuthScreenLayout
        title="This link is incomplete"
        intro="The address has no token in it. Mail clients sometimes cut a long link in half — open it from the message again, or paste the whole line into the address bar."
        footer={<SignInLink />}
      />
    );
  }

  if (verify.isSuccess) {
    return (
      <AuthScreenLayout
        title="Email verified"
        intro="Your address is confirmed. There is nothing else to do here."
      >
        <Link to="/traces" className="text-brand-text text-sm hover:underline">
          Go to your traces
        </Link>
      </AuthScreenLayout>
    );
  }

  if (verify.isError) {
    return (
      <AuthScreenLayout
        title="That link did not work"
        intro={describeLinkRefusal(verify.error)}
        footer={<SignInLink />}
      />
    );
  }

  return (
    <AuthScreenLayout title="Checking your link" intro="One moment.">
      <p role="status" className="text-ink-dim text-sm">
        Checking…
      </p>
    </AuthScreenLayout>
  );
}

function SignInLink() {
  return (
    <p>
      <Link to="/login" className="text-brand-text hover:underline">
        Sign in
      </Link>{" "}
      to carry on. Verification is not required to use nexovue.
    </p>
  );
}
