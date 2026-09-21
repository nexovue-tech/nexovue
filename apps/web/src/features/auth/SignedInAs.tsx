import { useSession } from "@/shared/identity";

import { useSignOut } from "./api";

/**
 * Who is signed in, and how to stop being signed in.
 *
 * **The console had no ordinary sign-out.** The only way out was *Sign out
 * everywhere* under Settings → Account, which ends the session on every phone and
 * laptop the account is signed in on — the right control for "somebody has my
 * password", and the wrong one for "I am leaving this machine".
 *
 * The address above the action, because the question anyone asks before pressing
 * it is *which account*. Truncated rather than wrapped: a long address must not
 * push the control it belongs to out of the footer.
 */
export function SignedInAs() {
  const session = useSession();
  const signOut = useSignOut();
  const email = session.data?.user.email;

  return (
    <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
      <span className="min-w-0 truncate text-ink-dim text-xs" title={email}>
        {email ?? ""}
      </span>
      <button
        type="button"
        onClick={() => signOut.mutate()}
        disabled={signOut.isPending}
        // Sentence case, not the mono caps the rest of this footer uses: those are
        // labels, this is an action, and the caps cost the address the room it
        // needs to be read.
        className="-mr-1.5 shrink-0 rounded px-1.5 py-0.5 text-ink-dim text-xs hover:bg-hover hover:text-ink focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1 disabled:opacity-60"
      >
        {signOut.isPending ? "Signing out…" : "Sign out"}
      </button>
    </div>
  );
}
