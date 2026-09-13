import { useNavigate } from "@tanstack/react-router";
import { type FormEvent, type ReactNode, useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { clearSession } from "@/shared/session";

import {
  PASSWORD_MIN_LENGTH,
  useChangeOwnPassword,
  useRenameSelf,
  useSession,
  useSignOutEverywhere,
} from "./api";
import { describeFormProblem } from "./problem-fields";
import { SIGNED_OUT_EVERYWHERE } from "./search";

/**
 * Your own account: what you are called, the password on it, and the one control that
 * ends every session at once.
 *
 * **The two session behaviours are the substance of this screen, and they are
 * deliberately opposite.** Changing the password keeps the session you are sitting in
 * and ends every other one; sign-out-everywhere ends this one too. Both are stated
 * before the customer commits, not only after, because a control that surprises you at
 * the moment it fires is one you stop trusting.
 *
 * Email change and timezone are the rest of P1-72 and are not built — no API exists for
 * either yet, so the address below is shown and not offered as a field.
 */
export function ProfileScreen() {
  const session = useSession();

  if (session.isPending) {
    return (
      <div className="px-4 pt-5">
        <SkeletonRows rows={6} label="Loading your account" />
      </div>
    );
  }

  if (session.isError || session.data === undefined) {
    return (
      <div className="px-4 pt-5">
        <InlineFailure
          title="Your account could not be loaded"
          error={session.error}
          onRetry={() => void session.refetch()}
        />
      </div>
    );
  }

  const { user, organization } = session.data;

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">
          Profile &amp; security
        </h1>
        <p className="mt-1 max-w-2xl text-ink-dim text-sm leading-relaxed">
          Signed in as <span className="text-ink">{user.email}</span> in{" "}
          <span className="text-ink">{organization.name}</span>. The address cannot be changed here
          yet — changing it has to re-verify, and that is not built.
        </p>
      </header>

      <NameSection currentName={user.name} />
      <PasswordSection />
      <SignOutEverywhereSection />
    </div>
  );
}

/* ------------------------------------------------------------------- the name */

function NameSection({ currentName }: { currentName: string | null }) {
  const rename = useRenameSelf();
  const [name, setName] = useState(currentName ?? "");

  const problem = describeFormProblem(rename.error);
  // Comparing against the stored value rather than tracking "dirty": a name typed back
  // to what it already was is not a change, and a Save that does nothing reads as broken.
  const changed = name.trim() !== (currentName ?? "");

  const guard = useUnsavedChanges(changed);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (changed) rename.mutate(name);
  }

  return (
    <Section
      title="Your name"
      blurb="What colleagues see beside anything you did. Leave it empty and the account shows your address instead."
    >
      <UnsavedChangesPrompt guard={guard} what="your name" />
      <form onSubmit={onSubmit} noValidate className="max-w-sm">
        {problem.message ? <FormProblem message={problem.message} /> : null}

        <TextField
          label="Name"
          name="name"
          value={name}
          onChange={setName}
          required={false}
          autoComplete="name"
          error={problem.fieldErrors.name}
        />

        <div className="mt-3 flex items-center gap-3">
          <Button
            type="submit"
            pending={rename.isPending}
            pendingLabel="Saving…"
            disabled={!changed}
          >
            Save name
          </Button>
          {rename.isSuccess && !changed ? (
            <span role="status" className="text-ink-dim text-xs">
              Saved.
            </span>
          ) : null}
        </div>
      </form>
    </Section>
  );
}

/* --------------------------------------------------------------- the password */

/**
 * Said before the change, not only after. The asymmetry with sign-out-everywhere below
 * is the whole reason this sentence exists.
 */
const PASSWORD_SESSION_RULE =
  "Changing it signs out every other session on this account — every other browser, phone and device. You stay signed in here, because entering the current password is how you prove this device is yours.";

const PASSWORD_CHANGED_CONFIRMATION =
  "Your password is changed, and every other session on this account has been signed out. You are still signed in here; this session was replaced as part of the change, so there is nothing for you to do.";

const CURRENT_PASSWORD_REQUIRED = "Enter the password on the account now.";

function PasswordSection() {
  const changePassword = useChangeOwnPassword();

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  // Only what this screen can tell without asking. Everything else is the API's answer.
  const [refusals, setRefusals] = useState<Record<string, string>>({});

  const problem = describeFormProblem(changePassword.error);
  const fieldErrors = { ...problem.fieldErrors, ...refusals };

  // A half-typed password change is unsaved work like any other, so AC19 covers it.
  // Worth revisiting if a password manager's autofill starts making this true without
  // anybody typing — a warning that fires on its own is the kind people stop reading.
  const guard = useUnsavedChanges(currentPassword !== "" || newPassword !== "");

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    // Refused here rather than sent: this endpoint sits on the strict auth tier because
    // it verifies a credential, and a blank submit would spend that allowance to be told
    // something the screen already knows.
    const missing: Record<string, string> = {};
    if (currentPassword.length === 0) missing.current_password = CURRENT_PASSWORD_REQUIRED;
    if (newPassword.length < PASSWORD_MIN_LENGTH)
      missing.new_password = `At least ${PASSWORD_MIN_LENGTH} characters.`;

    setRefusals(missing);
    if (Object.keys(missing).length > 0) return;

    changePassword.mutate(
      { current_password: currentPassword, new_password: newPassword },
      {
        onSuccess: () => {
          setCurrentPassword("");
          setNewPassword("");
        },
      },
    );
  }

  return (
    <Section title="Password" blurb={PASSWORD_SESSION_RULE}>
      {changePassword.isSuccess ? (
        <p
          role="status"
          className="mb-4 max-w-2xl border-brand-line border-l-2 py-1 pl-3 text-ink text-sm leading-relaxed"
        >
          {PASSWORD_CHANGED_CONFIRMATION}
        </p>
      ) : null}

      <UnsavedChangesPrompt guard={guard} what="your password" />
      <form onSubmit={onSubmit} noValidate className="max-w-sm">
        {problem.message ? <FormProblem message={problem.message} /> : null}

        <div className="flex flex-col gap-4">
          <TextField
            label="Current password"
            name="current_password"
            type="password"
            value={currentPassword}
            onChange={setCurrentPassword}
            autoComplete="current-password"
            error={fieldErrors.current_password}
          />
          <TextField
            label="New password"
            name="new_password"
            type="password"
            value={newPassword}
            onChange={setNewPassword}
            autoComplete="new-password"
            hint={`At least ${PASSWORD_MIN_LENGTH} characters.`}
            error={fieldErrors.new_password}
          />
        </div>

        <div className="mt-4">
          <Button type="submit" pending={changePassword.isPending} pendingLabel="Changing…">
            Change password
          </Button>
        </div>
      </form>
    </Section>
  );
}

/* ------------------------------------------------------ sign out everywhere */

const SIGN_OUT_EVERYWHERE_BLURB =
  "Ends every session on this account, including this one. Reach for it when you think somebody else may be signed in as you — a shared machine you walked away from, a phone you no longer have.";

function SignOutEverywhereSection() {
  const navigate = useNavigate();
  const signOut = useSignOutEverywhere();
  const [confirming, setConfirming] = useState(false);

  /**
   * The 204 clears the cookie, so this session is already dead by the time we get here.
   * Going to /login ourselves — with the notice that says so — is what keeps the app
   * from meeting its own 401 on the next request and reporting a lapsed session for
   * something the customer just asked for.
   */
  function endEverySession() {
    signOut.mutate(undefined, {
      onSuccess: async () => {
        clearSession();
        await navigate({ to: "/login", search: { notice: SIGNED_OUT_EVERYWHERE }, replace: true });
      },
    });
  }

  return (
    <Section title="Sign out everywhere" blurb={SIGN_OUT_EVERYWHERE_BLURB}>
      {signOut.isError ? (
        <div className="mb-3 max-w-2xl">
          <InlineFailure title="The sessions could not be ended" error={signOut.error} />
        </div>
      ) : null}

      {confirming ? (
        <div className="max-w-2xl border-critical-solid border-l-2 bg-critical-subtle px-3 py-3">
          <p className="font-medium text-ink text-sm">Sign out of every session, including this?</p>
          <p className="mt-1 text-ink-dim text-sm leading-relaxed">
            You will land on the sign-in screen straight away and need your password to come back.
            Nothing is deleted and the password itself does not change — every session that exists
            simply stops being accepted.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <Button pending={signOut.isPending} pendingLabel="Ending…" onClick={endEverySession}>
              Yes, sign me out everywhere
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="secondary" onClick={() => setConfirming(true)}>
          Sign out everywhere
        </Button>
      )}
    </Section>
  );
}

/* --------------------------------------------------------------- furniture */

function Section({
  title,
  blurb,
  children,
}: {
  title: string;
  blurb: string;
  children: ReactNode;
}) {
  return (
    <section className="shrink-0 border-line-subtle border-t px-4 py-5">
      <h2 className="font-semibold font-serif text-ink text-lg tracking-tight">{title}</h2>
      <p className="mt-1 mb-4 max-w-2xl text-ink-dim text-sm leading-relaxed">{blurb}</p>
      {children}
    </section>
  );
}

/** The form-wide half of a refusal, in the same register as the bare auth screens. */
function FormProblem({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="mb-4 border-critical-solid border-l-2 py-1 pl-3 text-critical-text text-sm"
    >
      {message}
    </p>
  );
}
