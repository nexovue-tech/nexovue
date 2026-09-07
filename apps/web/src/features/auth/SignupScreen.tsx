import { useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { Button } from "@/shared/components/Button";
import { TextField } from "@/shared/components/TextField";
import { rememberFirstSetup } from "@/shared/session";

import { AuthScreenLayout, FormMessage } from "./AuthScreenLayout";
import { PASSWORD_MIN_LENGTH, signup } from "./api";
import { describeFormProblem } from "./problem-fields";

/**
 * One form, one transaction behind it, and the person arrives signed in. The API issues
 * the ingest key here and never again, so it is handed straight to the next screen.
 */
export function SignupScreen() {
  const navigate = useNavigate();

  const [companyName, setCompanyName] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const createAccount = useMutation({
    mutationKey: ["auth", "signup"],
    mutationFn: signup,
    onSuccess: async (created) => {
      rememberFirstSetup({
        apiKey: created.api_key,
        otlpEndpoint: created.otlp_endpoint,
        projectId: created.project.id,
      });
      await navigate({ to: "/onboarding", replace: true });
    },
  });

  const problem = describeFormProblem(createAccount.error);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    createAccount.mutate({
      organization_name: companyName,
      email,
      password,
      name: name.trim() === "" ? undefined : name.trim(),
    });
  }

  return (
    <AuthScreenLayout
      title="Start sending traces"
      intro="Four details, and the next screen has the two lines that point your services here."
      footer={
        <p>
          Already set up?{" "}
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
            label="Company"
            name="organization_name"
            value={companyName}
            onChange={setCompanyName}
            autoComplete="organization"
            error={problem.fieldErrors.organization_name}
          />

          <TextField
            label="Your name"
            name="name"
            value={name}
            onChange={setName}
            autoComplete="name"
            required={false}
            hint="Optional."
            error={problem.fieldErrors.name}
          />

          <TextField
            label="Email"
            name="email"
            type="email"
            value={email}
            onChange={setEmail}
            autoComplete="email"
            error={problem.fieldErrors.email}
          />

          <TextField
            label="Password"
            name="password"
            type="password"
            value={password}
            onChange={setPassword}
            autoComplete="new-password"
            hint={`At least ${PASSWORD_MIN_LENGTH} characters.`}
            error={problem.fieldErrors.password}
          />

          <div className="mt-1">
            <Button type="submit" pending={createAccount.isPending} pendingLabel="Creating…">
              Create account
            </Button>
          </div>
        </div>
      </form>
    </AuthScreenLayout>
  );
}
