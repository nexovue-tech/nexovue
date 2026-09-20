import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { TextField } from "@/shared/components/TextField";

import { useEnterAdminMode } from "./api";

/**
 * The gate on the platform surface (`docs/changes/super-admin-in-the-console/`).
 *
 * The admin screens live on the customer origin now, so a session that reaches
 * the console must not reach the platform on its own: an XSS anywhere in the
 * console would inherit it. Confirming a password — or a second-factor code —
 * opens the surface for thirty minutes, and it closes with the session.
 *
 * Shown in place of the screen rather than over it: what is behind this has not
 * loaded, and a dialog floating over an empty page suggests otherwise.
 */
export function ConfirmItIsYou({ onConfirmed }: { onConfirmed: () => void }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const confirm = useEnterAdminMode();

  return (
    <section aria-label="Confirm it is you" className="max-w-lg border border-line px-4 py-4">
      <h2 className="font-semibold font-serif text-ink text-lg tracking-tight">
        Confirm it is you
      </h2>
      <p className="mt-1 text-ink-dim text-sm leading-relaxed">
        The platform area needs your password again. It stays open for 30 minutes, and closes when
        you sign out.
      </p>

      {/* Neither field is required on its own — either one opens the area — so
          the browser must not hold the submit back for the empty one. */}
      <form
        className="mt-3 flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!password.trim() && !code.trim()) return;
          confirm.mutate(code.trim() ? { code: code.trim() } : { password }, {
            onSuccess: () => onConfirmed(),
          });
        }}
      >
        <TextField
          label="Password"
          name="password"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete="current-password"
          required={false}
        />
        <TextField
          label="Or a two-factor code"
          name="code"
          value={code}
          onChange={setCode}
          hint="If you have an authenticator enrolled, a code works instead of the password."
          required={false}
        />
        <div>
          <Button
            type="submit"
            pending={confirm.isPending}
            pendingLabel="Confirming…"
            disabled={!password.trim() && !code.trim()}
          >
            Confirm
          </Button>
        </div>
      </form>

      {confirm.error ? (
        <InlineFailure title="That did not open the platform area" error={confirm.error} />
      ) : null}
    </section>
  );
}
