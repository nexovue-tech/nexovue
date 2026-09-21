import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { Button } from "@/shared/components/Button";
import { TextField } from "@/shared/components/TextField";
import { OPERATOR_ROLE } from "@/shared/identity";
import { takeReturnTo } from "@/shared/session";

import { AuthScreenLayout, FormMessage, FormNotice } from "./AuthScreenLayout";
import { login, needsSecondFactor, sessionQueryKey, submitSecondFactor } from "./api";
import { describeFormProblem } from "./problem-fields";
import type { LoginNotice, LoginSearch } from "./search";
import { PASSWORD_WAS_RESET, SIGNED_OUT_EVERYWHERE } from "./search";

/** Where someone lands when they did not arrive from somewhere specific. */
const DEFAULT_DESTINATION = "/traces";

/**
 * nexovue's own operator has no project, so every customer screen answers "no
 * such project" for them — including the one above. Their console is the
 * platform area and nothing else (`docs/changes/console-review-fixes/`).
 */
const OPERATORS_DESTINATION = "/admin";

/**
 * What another screen sends someone here to read.
 *
 * Both say the session thing out loud, because in both cases it is the surprising half —
 * and the second one especially: arriving at a sign-in screen you did not expect reads
 * as a fault, so the copy has to name it as the control doing its job.
 */
const NOTICE_MESSAGE: Record<LoginNotice, string> = {
  [PASSWORD_WAS_RESET]:
    "Your password is changed. Every other session on this account has been signed out — sign in with the new password.",
  [SIGNED_OUT_EVERYWHERE]:
    "Every session on this account has been signed out, including this one. That is what you asked for, not a fault — sign in to start a fresh one.",
};

/**
 * Said above the code field, because a second-factor prompt is where somebody with a
 * dead phone gets stuck. The way out has to be on the screen, not in a help article.
 */
const SECOND_FACTOR_INTRO =
  "Open your authenticator app and enter the six-digit code. If you no longer have it, enter one of the recovery codes you saved instead — each works once.";

export function LoginScreen({ notice }: LoginSearch) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  /**
   * The challenge the API handed back when the password was right and not enough.
   * Held in state rather than pushed to a route of its own: it is one step of one
   * sign-in, it expires in minutes, and a URL somebody could bookmark or share would
   * be a URL carrying half a credential.
   */
  const [challenge, setChallenge] = useState<string | null>(null);

  async function landInside(session: Awaited<ReturnType<typeof submitSecondFactor>>) {
    queryClient.setQueryData(sessionQueryKey, session);
    // A remembered route is still honoured for a customer — it is where they
    // were going. The operator has nowhere else to be, and a remembered
    // customer screen would only refuse them.
    const destination =
      session.user.role === OPERATOR_ROLE
        ? OPERATORS_DESTINATION
        : (takeReturnTo() ?? DEFAULT_DESTINATION);
    await navigate({ to: destination, replace: true });
  }

  const signIn = useMutation({
    mutationKey: ["auth", "login"],
    mutationFn: login,
    onSuccess: async (outcome) => {
      if (needsSecondFactor(outcome)) {
        setChallenge(outcome.challenge);
        return;
      }
      await landInside(outcome);
    },
  });

  const problem = describeFormProblem(signIn.error);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    signIn.mutate({ email, password });
  }

  if (challenge !== null) {
    return (
      <SecondFactorStep
        challenge={challenge}
        onSignedIn={landInside}
        onStartOver={() => {
          setChallenge(null);
          setPassword("");
        }}
      />
    );
  }

  return (
    <AuthScreenLayout
      title="Sign in"
      intro="Your telemetry is waiting where you left it."
      footer={
        <div className="flex flex-col gap-2">
          <p>
            No account yet?{" "}
            <Link to="/signup" className="text-brand-text hover:underline">
              Start sending traces
            </Link>
            .
          </p>
          <p>
            <Link to="/forgot-password" className="text-brand-text hover:underline">
              Forgotten your password?
            </Link>
          </p>
        </div>
      }
    >
      <form onSubmit={onSubmit} noValidate>
        {notice ? <FormNotice message={NOTICE_MESSAGE[notice]} /> : null}
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

/**
 * The second half of a sign-in.
 *
 * **One field for both kinds of code**, because the customer knows either as "the
 * code"; the API tries the authenticator's and then the recovery codes.
 *
 * **The refusal is never narrowed.** A wrong code, an expired challenge and one whose
 * attempts are spent all come back as the same 401 with the same sentence, so that
 * somebody holding a stolen password cannot learn which half of their attack is
 * landing. The screen passes that message through and adds a way to start again,
 * because a spent challenge needs the password entering afresh.
 */
function SecondFactorStep({
  challenge,
  onSignedIn,
  onStartOver,
}: {
  challenge: string;
  onSignedIn: (session: Awaited<ReturnType<typeof submitSecondFactor>>) => Promise<void>;
  onStartOver: () => void;
}) {
  const [code, setCode] = useState("");

  const submit = useMutation({
    mutationKey: ["auth", "second-factor"],
    mutationFn: submitSecondFactor,
    onSuccess: onSignedIn,
  });

  const problem = describeFormProblem(submit.error);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (code.trim() === "") return;
    submit.mutate({ challenge, code: code.trim() });
  }

  return (
    <AuthScreenLayout
      title="One more step"
      intro={SECOND_FACTOR_INTRO}
      footer={
        <p>
          Wrong account?{" "}
          <button type="button" onClick={onStartOver} className="text-brand-text hover:underline">
            Start again
          </button>
          .
        </p>
      }
    >
      <form onSubmit={onSubmit} noValidate>
        {problem.message ? <FormMessage message={problem.message} /> : null}

        <div className="flex flex-col gap-4">
          <TextField
            label="Code"
            name="code"
            value={code}
            onChange={setCode}
            /* The browser fills a one-time code from a message or a password manager;
               it is not a password and must not be offered to be saved as one. */
            autoComplete="one-time-code"
            error={problem.fieldErrors.code}
          />

          <div className="mt-1">
            <Button type="submit" pending={submit.isPending} pendingLabel="Checking…">
              Sign in
            </Button>
          </div>
        </div>
      </form>
    </AuthScreenLayout>
  );
}
