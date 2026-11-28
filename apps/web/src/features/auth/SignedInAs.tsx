import { ArrowUpRight } from "lucide-react";
import { type ReactNode, useId } from "react";

import { useSession } from "@/shared/identity";
import { SETUP_DOCS_PATH } from "@/shared/setup-docs";
import { ThemeToggle } from "@/shared/theme/ThemeToggle";

import { useSignOut } from "./api";

const ROLE_WORDS: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  viewer: "Viewer",
};

/** Two letters for the account row: a name's first two words, else the address's start. */
export function initialsOf(name: string | null | undefined, email: string | undefined): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return `${words[0]?.[0] ?? ""}${words[1]?.[0] ?? ""}`.toUpperCase();
  if (words.length === 1) return (words[0] ?? "").slice(0, 2).toUpperCase();
  return (email ?? "").slice(0, 2).toUpperCase();
}

/**
 * The sidebar's one account row (approved sketch G, 2026-11-28): who is signed in, in which
 * role and organization, a way to Settings (the sidebar's, passed in, since this holds no
 * route), and a menu with the rest. It replaces three
 * stacked boxes (the cut-off address with Sign out, the documentation, the theme).
 *
 * The address stays one click away, in the menu above Sign out, because the question before
 * pressing it is *which account*. The menu is the platform's popover: it closes on Escape
 * and on a click outside, with no library.
 */
export function SignedInAs({ settings }: { settings?: ReactNode }) {
  const session = useSession();
  const signOut = useSignOut();
  const menuId = useId();
  const user = session.data?.user;
  const email = user?.email;
  const name = user?.name?.trim() || email || "";
  const role = user ? (ROLE_WORDS[user.role] ?? user.role) : "";
  const organization = session.data?.organization.name ?? "";

  return (
    <div className="flex min-w-0 flex-1 items-center gap-1">
      {/* The name is the menu's button: a separate ⋯ left the name too little room and cut
          "Asha Tester" to "Asha Tes…" (UX review, 2026-11-28). */}
      <button
        type="button"
        popoverTarget={menuId}
        aria-label={`Account menu for ${name}: documentation, theme, sign out`}
        className="flex min-h-11 min-w-0 flex-1 items-center gap-2 rounded px-1 text-left hover:bg-hover md:min-h-9"
      >
        <span
          aria-hidden="true"
          className="grid size-6 shrink-0 place-items-center rounded-full bg-raised font-semibold text-2xs text-ink"
        >
          {initialsOf(user?.name, email)}
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate font-semibold text-ink text-sm" title={name}>
            {name}
          </span>
          <span className="truncate text-ink-dim text-xs" title={organization}>
            {[role, organization].filter(Boolean).join(" · ")}
          </span>
        </span>
      </button>
      {settings}
      <div
        id={menuId}
        popover="auto"
        className="m-0 w-60 rounded-sm border border-line bg-surface p-0 text-sm shadow-overlay [inset:auto_auto_3.5rem_0.75rem]"
      >
        <p className="truncate border-line border-b px-3 py-2 text-ink-dim text-xs" title={email}>
          Signed in as {email ?? ""}
        </p>
        <a
          href={SETUP_DOCS_PATH}
          target="_blank"
          rel="noreferrer"
          className="flex min-h-11 items-center gap-1.5 px-3 text-ink hover:bg-hover md:min-h-9"
        >
          Setup documentation
          <ArrowUpRight size={12} aria-hidden="true" />
        </a>
        <div className="flex min-h-11 items-center justify-between gap-2 px-3 md:min-h-9">
          <span className="text-ink">Theme</span>
          <ThemeToggle />
        </div>
        <button
          type="button"
          onClick={() => signOut.mutate()}
          disabled={signOut.isPending}
          className="flex min-h-11 w-full items-center border-line border-t px-3 text-left text-ink hover:bg-hover disabled:opacity-60 md:min-h-9"
        >
          {signOut.isPending ? "Signing out…" : "Sign out"}
        </button>
      </div>
    </div>
  );
}
