import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { Button } from "@/shared/components/Button";
import { TextField } from "@/shared/components/TextField";
import { takeReturnTo } from "@/shared/session";

import { AuthScreenLayout, FormMessage } from "./AuthScreenLayout";
import { login, sessionQueryKey } from "./api";
import { describeFormProblem } from "./problem-fields";

/** Where someone lands when they did not arrive from somewhere specific. */
const DEFAULT_DESTINATION = "/traces";

export function LoginScreen() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const signIn = useMutation({
    mutationKey: ["auth", "login"],
    mutationFn: login,
    onSuccess: async (session) => {
      queryClient.setQueryData(sessionQueryKey, session);
      await navigate({ to: takeReturnTo() ?? DEFAULT_DESTINATION, replace: true });
    },
  });

  const problem = describeFormProblem(signIn.error);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    signIn.mutate({ email, password });
  }

  return (
    <AuthScreenLayout
      title="Sign in"
      intro="Your telemetry is waiting where you left it."
      footer={
        <p>
          No account yet?{" "}
          <Link to="/signup" className="text-brand-text hover:underline">
            Start sending traces
          </Link>
          .
        </p>
      }
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

          <TextField
            label="Password"
            name="password"
            type="password"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
            error={problem.fieldErrors.password}
          />

          <div className="mt-1">
            <Button type="submit" pending={signIn.isPending} pendingLabel="Signing in…">
              Sign in
            </Button>
          </div>
        </div>
      </form>
    </AuthScreenLayout>
  );
}
