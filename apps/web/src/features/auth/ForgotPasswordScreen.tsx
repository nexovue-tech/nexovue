import { useMutation } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { Button } from "@/shared/components/Button";
import { TextField } from "@/shared/components/TextField";

import { AuthScreenLayout, FormMessage } from "./AuthScreenLayout";
import { requestPasswordReset } from "./api";
import { describeFormProblem } from "./problem-fields";

/**
 * Ask for a reset link.
 *
 * **The confirmation below says nothing about whether the address has an account, and
 * cannot be changed to.** The API answers 202 either way for exactly this reason; a
 * screen that showed "no such account" would hand the whole endpoint's security property
 * back to whoever asked. It is not given the address it was typed either, so there is
 * nothing here that could vary with one.
 */
export function ForgotPasswordScreen() {
  const [email, setEmail] = useState("");

  const requestLink = useMutation({
    mutationKey: ["auth", "password-reset"],
    mutationFn: requestPasswordReset,
  });

  const problem = describeFormProblem(requestLink.error);

  if (requestLink.isSuccess) {
    return (
      <AuthScreenLayout
        title="Check your inbox"
        intro="If that address has an account, a link to set a new password is on its way. It works once, and the mail says how long it lasts."
        footer={<BackToSignIn />}
      />
    );
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    requestLink.mutate(email);
  }

  return (
    <AuthScreenLayout
      title="Reset your password"
      intro="Give us the address on the account and we will mail a link that sets a new password."
      footer={<BackToSignIn />}
    >
      <form onSubmit={onSubmit} noValidate>
        {problem.message ? <FormMessage message={problem.message} /> : null}

        <div className="flex flex-col gap-4">
          <TextField
            label="Email"
            name="email"
            type="email"
            value={email}
            onChange={setEmail}
            autoComplete="username"
            error={problem.fieldErrors.email}
          />

          <div className="mt-1">
            <Button type="submit" pending={requestLink.isPending} pendingLabel="Sending…">
              Send the link
            </Button>
          </div>
        </div>
      </form>
    </AuthScreenLayout>
  );
}

function BackToSignIn() {
  return (
    <p>
      <Link to="/login" className="text-brand-text hover:underline">
        Back to sign in
      </Link>
      .
    </p>
  );
}
