import { useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { Button } from "@/shared/components/Button";
import { TextField } from "@/shared/components/TextField";

import { AuthScreenLayout, FormMessage } from "./AuthScreenLayout";
import { confirmPasswordReset, PASSWORD_MIN_LENGTH } from "./api";
import { describeFormProblem } from "./problem-fields";
import { PASSWORD_WAS_RESET } from "./search";

/**
 * Where a reset link lands: one field, and then the sign-in screen.
 *
 * It deliberately does not sign the customer in. The API issues no session here — the
 * person holding the link proved they control the address, and signing in with the
 * password they just chose is the step that tells them it worked. Working around that
 * would also mean holding a session the reset had just invalidated.
 */
export function ResetPasswordScreen({ token }: { token?: string }) {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");

  const reset = useMutation({
    mutationKey: ["auth", "password-reset-confirm"],
    mutationFn: confirmPasswordReset,
    onSuccess: async () => {
      // `replace` so Back cannot return to a form holding a token that is now spent.
      await navigate({ to: "/login", search: { notice: PASSWORD_WAS_RESET }, replace: true });
    },
  });

  const problem = describeFormProblem(reset.error);

  if (token === undefined) {
    return (
      <AuthScreenLayout
        title="This link is incomplete"
        intro="The address has no token in it, so there is no account to set a password for. Mail clients sometimes cut a long link in half — open it from the message again, or ask for a new link."
        footer={
          <p>
            <Link to="/forgot-password" className="text-brand-text hover:underline">
              Send me a new link
            </Link>
            .
          </p>
        }
      />
    );
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (token === undefined) return;
    reset.mutate({ token, password });
  }

  return (
    <AuthScreenLayout
      title="Choose a new password"
      intro="Setting it here signs out every other session on the account, on every device."
      footer={
        <p>
          Remembered it after all?{" "}
          <Link to="/login" className="text-brand-text hover:underline">
            Sign in
          </Link>
          .
        </p>
      }
    >
      <form onSubmit={onSubmit} noValidate>
        {problem.message ? <FormMessage message={problem.message} /> : null}

        <div className="flex flex-col gap-4">
          <TextField
            label="New password"
            name="password"
            type="password"
            value={password}
            onChange={setPassword}
            autoComplete="new-password"
            hint={`At least ${PASSWORD_MIN_LENGTH} characters.`}
            error={problem.fieldErrors.password}
          />

          <div className="mt-1">
            <Button type="submit" pending={reset.isPending} pendingLabel="Saving…">
              Set password
            </Button>
          </div>
        </div>
      </form>
    </AuthScreenLayout>
  );
}
