import { useNavigate } from "@tanstack/react-router";
import { type FormEvent, type ReactNode, useState } from "react";

import { Button } from "@/shared/components/Button";
import { CopyButton } from "@/shared/components/CopyButton";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { UNKNOWN } from "@/shared/format/number";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { clearSession } from "@/shared/session";

import {
  type ActiveSession,
  PASSWORD_MIN_LENGTH,
  useActiveSessions,
  useBeginSecondFactorEnrolment,
  useChangeOwnPassword,
  useConfirmSecondFactor,
  useDisableSecondFactor,
  useEndSession,
  useRenameSelf,
  useSecondFactor,
  useSession,
  useSignOutEverywhere,
} from "./api";
import { describeFormProblem } from "./problem-fields";
import { SIGNED_OUT_EVERYWHERE } from "./search";

/**
 * Your own account: what you are called, the password on it, the second factor in
 * front of it, what is signed in as you, and the control that ends all of it at once.
 *
 * **The session controls are the substance of this screen, and they are deliberately
 * different from each other.** Changing the password keeps the session you are sitting
 * in and ends every other one; ending one device ends exactly that one;
 * sign-out-everywhere ends this one too. Each is stated before the customer commits,
 * not only after, because a control that surprises you at the moment it fires is one
 * you stop trusting.
 *
 * **The order is the order somebody works down it when they are worried.** What is
 * signed in comes before the blunt instrument, because "end the one I do not
 * recognise" is almost always the right answer and "sign out of everything" is the one
 * people reach for only because it was the only one on offer.
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
      <SecondFactorSection />
      <ActiveSessionsSection />
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

/* ------------------------------------------------------- the second factor */

const SECOND_FACTOR_BLURB =
  "A code from an app on your phone, asked for after your password. It is the difference between somebody who has stolen your password getting in and not getting in.";

const RECOVERY_CODES_BLURB =
  "Save these somewhere that is not the phone holding your authenticator — a password manager, or paper. Each one signs you in once if you lose the app. They are shown here and nowhere else, because only hashes are stored.";

/**
 * Turning the second factor on is three steps and the middle one is the point: the
 * account changes when a code proves the secret arrived, not when the secret is shown.
 * A customer who mistypes it into their authenticator is refused at that step instead
 * of being locked out of the product by a control they were trying to add.
 */
type EnrolmentStage = "off" | "scanning" | "codes";

function SecondFactorSection() {
  const state = useSecondFactor();
  const [stage, setStage] = useState<EnrolmentStage>("off");

  const begin = useBeginSecondFactorEnrolment();
  const confirm = useConfirmSecondFactor();
  const disable = useDisableSecondFactor();

  const [currentPassword, setCurrentPassword] = useState("");
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[]>([]);

  function startEnrolment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    begin.mutate(currentPassword, {
      onSuccess: () => {
        setCurrentPassword("");
        setStage("scanning");
      },
    });
  }

  function finishEnrolment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    confirm.mutate(code, {
      onSuccess: (issued) => {
        setCode("");
        setCodes(issued.recovery_codes);
        setStage("codes");
      },
    });
  }

  function turnOff(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    disable.mutate(currentPassword, { onSuccess: () => setCurrentPassword("") });
  }

  if (state.isPending) {
    return (
      <Section title="Two-factor authentication" blurb={SECOND_FACTOR_BLURB}>
        <SkeletonRows rows={2} label="Loading your second factor" />
      </Section>
    );
  }

  if (state.isError || state.data === undefined) {
    return (
      <Section title="Two-factor authentication" blurb={SECOND_FACTOR_BLURB}>
        <div className="max-w-2xl">
          <InlineFailure
            title="Your second factor could not be loaded"
            error={state.error}
            onRetry={() => void state.refetch()}
          />
        </div>
      </Section>
    );
  }

  // Shown once, at the moment the factor is armed. Deliberately not re-fetchable:
  // there is nothing to fetch, and a screen that offered to show them again would be
  // promising something the API cannot do.
  if (stage === "codes") {
    return (
      <Section title="Your recovery codes" blurb={RECOVERY_CODES_BLURB}>
        <div className="max-w-2xl border-brand-line border-l-2 py-2 pl-3">
          <ul className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-ink text-sm">
            {codes.map((recoveryCode) => (
              <li key={recoveryCode}>{recoveryCode}</li>
            ))}
          </ul>
          <div className="mt-3 flex items-center gap-2">
            <CopyButton value={codes.join("\n")} label="Copy recovery codes" />
            <Button
              variant="secondary"
              onClick={() => {
                setCodes([]);
                setStage("off");
              }}
            >
              I have saved them
            </Button>
          </div>
        </div>
      </Section>
    );
  }

  if (state.data.enabled) {
    const problem = describeFormProblem(disable.error);
    return (
      <Section title="Two-factor authentication" blurb={SECOND_FACTOR_BLURB}>
        <p role="status" className="mb-4 max-w-2xl text-ink text-sm leading-relaxed">
          On since {formatTimestamp(state.data.confirmed_at ?? "")}.{" "}
          {state.data.recovery_codes_remaining} of your recovery codes are unused.
        </p>
        <form onSubmit={turnOff} noValidate className="max-w-sm">
          {problem.message ? <FormProblem message={problem.message} /> : null}
          <TextField
            label="Current password"
            name="current_password"
            type="password"
            value={currentPassword}
            onChange={setCurrentPassword}
            autoComplete="current-password"
            error={problem.fieldErrors.current_password}
            hint="Asked for here as well as when you switched it on: removing the protection has to be as hard as adding it."
          />
          <div className="mt-4">
            <Button
              type="submit"
              variant="secondary"
              pending={disable.isPending}
              pendingLabel="Turning off…"
            >
              Turn off two-factor authentication
            </Button>
          </div>
        </form>
      </Section>
    );
  }

  if (stage === "scanning" && begin.data !== undefined) {
    const problem = describeFormProblem(confirm.error);
    return (
      <Section
        title="Two-factor authentication"
        blurb="Add this secret to your authenticator app, then enter the code it shows. Nothing changes on your account until that code is accepted."
      >
        <div className="mb-4 max-w-2xl border-line-subtle border-l-2 py-2 pl-3">
          <p className="font-mono text-ink text-sm break-all select-all">{begin.data.secret}</p>
          <div className="mt-2">
            <CopyButton value={begin.data.provisioning_uri} label="Copy setup link" />
          </div>
        </div>
        <form onSubmit={finishEnrolment} noValidate className="max-w-sm">
          {problem.message ? <FormProblem message={problem.message} /> : null}
          <TextField
            label="Code from your app"
            name="code"
            value={code}
            onChange={setCode}
            autoComplete="one-time-code"
            error={problem.fieldErrors.code}
          />
          <div className="mt-4 flex items-center gap-2">
            <Button type="submit" pending={confirm.isPending} pendingLabel="Checking…">
              Turn it on
            </Button>
            <Button variant="secondary" onClick={() => setStage("off")}>
              Cancel
            </Button>
          </div>
        </form>
      </Section>
    );
  }

  const problem = describeFormProblem(begin.error);
  return (
    <Section title="Two-factor authentication" blurb={SECOND_FACTOR_BLURB}>
      <form onSubmit={startEnrolment} noValidate className="max-w-sm">
        {problem.message ? <FormProblem message={problem.message} /> : null}
        <TextField
          label="Current password"
          name="current_password"
          type="password"
          value={currentPassword}
          onChange={setCurrentPassword}
          autoComplete="current-password"
          error={problem.fieldErrors.current_password}
          hint="Asked for so that a session somebody else is holding cannot switch this on and lock you out."
        />
        <div className="mt-4">
          <Button type="submit" pending={begin.isPending} pendingLabel="Preparing…">
            Set up two-factor authentication
          </Button>
        </div>
      </form>
    </Section>
  );
}

/* ------------------------------------------------------- what is signed in */

const SESSIONS_BLURB =
  "Every browser, phone and device signed in as you. Look here first when you think somebody else is — then end the one you do not recognise, rather than signing everything out.";

/**
 * **What this list deliberately does not show.** No raw browser string and no exact
 * address: the API reduces both before storing them, because whoever is signed in can
 * read this — and on the day this screen matters, that may be the intruder. A browser
 * family and a network are enough to recognise your own laptop and not enough to find
 * out where its owner sits.
 */
function ActiveSessionsSection() {
  const sessions = useActiveSessions();
  const endSession = useEndSession();

  if (sessions.isPending) {
    return (
      <Section title="Signed-in devices" blurb={SESSIONS_BLURB}>
        <SkeletonRows rows={3} label="Loading what is signed in" />
      </Section>
    );
  }

  if (sessions.isError || sessions.data === undefined) {
    return (
      <Section title="Signed-in devices" blurb={SESSIONS_BLURB}>
        <div className="max-w-2xl">
          <InlineFailure
            title="What is signed in could not be loaded"
            error={sessions.error}
            onRetry={() => void sessions.refetch()}
          />
        </div>
      </Section>
    );
  }

  return (
    <Section title="Signed-in devices" blurb={SESSIONS_BLURB}>
      {endSession.isError ? (
        <div className="mb-3 max-w-2xl">
          <InlineFailure title="That session could not be ended" error={endSession.error} />
        </div>
      ) : null}

      <ul className="max-w-2xl divide-y divide-line-subtle border-line-subtle border-t border-b">
        {sessions.data.map((entry) => (
          <SessionRow
            key={entry.id}
            session={entry}
            pending={endSession.isPending && endSession.variables === entry.id}
            onEnd={() => endSession.mutate(entry.id)}
          />
        ))}
      </ul>
    </Section>
  );
}

function SessionRow({
  session,
  pending,
  onEnd,
}: {
  session: ActiveSession;
  pending: boolean;
  onEnd: () => void;
}) {
  return (
    <li className="flex items-center justify-between gap-4 py-3">
      <div className="min-w-0">
        <p className="text-ink text-sm">
          {/* An unrecognised client is an em dash like every other missing reading,
              rather than a device name nobody owns. */}
          {session.client ?? "Unknown device"}
          {session.current ? (
            <span className="ml-2 font-mono text-2xs text-brand-text uppercase tracking-caps">
              This device
            </span>
          ) : null}
        </p>
        <p className="mt-0.5 text-ink-dim text-xs">
          {session.network ?? UNKNOWN} · last used {formatRelativeTime(session.last_seen_at)} ·
          started {formatTimestamp(session.created_at)}
        </p>
      </div>
      <Button variant="secondary" pending={pending} pendingLabel="Ending…" onClick={onEnd}>
        {session.current ? "End this session" : "End session"}
      </Button>
    </li>
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
