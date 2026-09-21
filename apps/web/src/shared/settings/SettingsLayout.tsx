import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useMemo, useState } from "react";

import { useSession } from "@/shared/identity";
import { useNothingLive } from "@/shared/refresh";

import { SETTINGS_GROUPS, type SettingsPath } from "./settings-nav";

/**
 * One rail, one pane, for every preference screen.
 *
 * **The account screens were five unrelated pages that happened to share a
 * sidebar heading.** Each set its own width, its own padding and its own idea of
 * how much prose a setting needs, so moving between them read as moving between
 * products. They now share a container, and the rail is what says what else
 * exists — which the main sidebar could only do by listing settings beside
 * telemetry screens.
 *
 * Routed pages rather than a modal over the console, which is the one place this
 * departs from the reference it takes its grammar from. Ingest keys and projects
 * are *work* — minting a key, copying two variables, deleting a project and
 * reading the receipt — and a modal is the wrong container for something a
 * customer spends minutes inside.
 */
export function SettingsLayout({
  title,
  description,
  children,
}: {
  title: string;
  /** One or two lines. What this screen is for, not how to use it. */
  description?: ReactNode;
  children: ReactNode;
}) {
  // Nothing on a settings screen arrives on its own, so the top bar offers no
  // interval over it.
  useNothingLive();

  return (
    // **The rail stays; the setting scrolls.** One scroll container held both, so
    // reading to the bottom of the audit log took the list of settings off screen
    // with it and coming back meant scrolling up past a page of rows. At `md` and
    // up the row is the height of the pane and each column scrolls on its own,
    // which is what a fixed rail means. Below `md` the rail is a collapsed
    // disclosure rather than a column, so the page stays one scroller there —
    // two nested ones on a phone is how a list stops scrolling under a thumb.
    <div className="flex h-full min-h-0 flex-col overflow-y-auto md:overflow-hidden">
      {/* **The page uses the window; the prose inside it does not.**
          Left-aligned like the rest of the console — it used to be centred, which
          made the page's left edge jump every time somebody moved between Alerts
          and Settings. It is now uncapped, like every telemetry screen: a `7xl`
          cap left a wide window's right quarter empty, and a settings screen is as
          often a table — members, tokens, keys, the audit log, the usage ledger —
          as it is a form. The cap that matters is inward, on the sentences: a line
          of explanation carries `max-w-3xl`, roughly 90 characters, past which a
          reader loses the start of the next line. A table does not have to. */}
      <div className="flex w-full flex-col gap-8 px-4 py-6 md:h-full md:min-h-0 md:flex-row md:gap-10 md:py-0">
        <SettingsNav />

        {/* A column inside the shell's main landmark, not a second landmark. Two of
            them is invalid HTML and gives a screen reader's landmark list a duplicate
            to choose between; `AppShell` owns the page's one. */}
        <div className="min-w-0 flex-1 pb-10 md:h-full md:min-h-0 md:overflow-y-auto md:pt-6">
          <header>
            <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">{title}</h1>
            {description ? (
              <p className="mt-1 max-w-3xl text-ink-dim text-sm leading-relaxed">{description}</p>
            ) : null}
          </header>

          <div className="mt-6">{children}</div>
        </div>
      </div>
    </div>
  );
}

/**
 * Every settings screen, grouped, with a filter over the names.
 *
 * The filter earns its place at eleven entries rather than five: somebody looking
 * for "sessions" should not have to know it lives under Account, and a reader who
 * knows exactly where it is loses nothing by ignoring a box.
 */
function SettingsNav() {
  const [typed, setTyped] = useState("");
  // Collapsed on a phone. Open, the rail is a search box and thirteen links —
  // about 480px of navigation above the screen somebody actually asked for
  // (`docs/quality/console-review-2026-09-20.md`). At `md` and up it is a
  // column beside the content and is always shown.
  const [open, setOpen] = useState(false);
  const needle = typed.trim().toLowerCase();
  const permissions = useSession().data?.user.permissions;

  const groups = useMemo(
    () =>
      SETTINGS_GROUPS.map((group) => ({
        ...group,
        items: group.items.filter(
          (item) =>
            (item.requires === undefined || permissions?.includes(item.requires) === true) &&
            (needle === "" ||
              item.title.toLowerCase().includes(needle) ||
              item.keywords.some((word) => word.includes(needle))),
        ),
      })).filter((group) => group.items.length > 0),
    [needle, permissions],
  );

  return (
    // Scrolls on its own at `md`, so a rail longer than the window is still
    // reachable without moving the setting beside it.
    <nav
      aria-label="Settings"
      className="w-full shrink-0 md:h-full md:w-52 md:overflow-y-auto md:py-6"
    >
      <button
        type="button"
        onClick={() => setOpen((shown) => !shown)}
        aria-expanded={open}
        className="-mx-2 flex min-h-11 w-[calc(100%+1rem)] items-center justify-between rounded px-2 text-ink text-sm hover:bg-hover md:hidden"
      >
        <span className="font-medium">All settings</span>
        <span aria-hidden="true" className="text-ink-dim">
          {open ? "−" : "+"}
        </span>
      </button>

      <div className={`${open ? "block" : "hidden"} md:block`}>
        <input
          type="search"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          aria-label="Find a setting"
          placeholder="Find a setting"
          className="w-full border border-line bg-surface px-2 py-1 text-ink text-sm placeholder:text-ink-dim focus:border-brand focus:outline-none"
        />

        {groups.length === 0 ? (
          // Never a blank rail: a reader who mistyped would otherwise see a
          // settings screen with no way out of it.
          <p className="mt-4 text-ink-dim text-xs leading-relaxed">
            Nothing here is called that. Clear the box to see everything.
          </p>
        ) : (
          <ul className="mt-4 flex flex-col gap-5">
            {groups.map((group) => (
              <li key={group.label}>
                <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
                  {group.label}
                </p>
                <ul className="mt-1.5 flex flex-col">
                  {group.items.map((item) => (
                    <li key={item.path}>
                      <NavItem
                        path={item.path}
                        title={item.title}
                        onNavigate={() => setOpen(false)}
                      />
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </div>
    </nav>
  );
}

function NavItem({
  path,
  title,
  onNavigate,
}: {
  path: SettingsPath;
  title: string;
  onNavigate: () => void;
}) {
  return (
    <Link
      to={path}
      onClick={onNavigate}
      // `min-h-11` rather than padding alone: on a phone these are the tap
      // targets, and 28px is below what a finger reliably hits.
      className="-mx-2 flex min-h-11 items-center rounded px-2 text-ink-dim text-sm hover:bg-hover hover:text-ink md:min-h-0 md:py-1"
      activeProps={{ className: "bg-raised font-medium text-ink" }}
    >
      {title}
    </Link>
  );
}
