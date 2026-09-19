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
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      {/* **Centred, and capped once for every settings screen.** Left-aligned, the
          content ended a third of the way across a wide window and the rest read as
          missing rather than as margin; uncapped, a preference row stretched until its
          control was two hundred pixels from the label that names it. The cap is the
          same on every screen so the rail does not jump sideways as a reader moves
          between them. */}
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-5 py-6 md:flex-row md:gap-10">
        <SettingsNav />

        {/* A column inside the shell's main landmark, not a second landmark. Two of
            them is invalid HTML and gives a screen reader's landmark list a duplicate
            to choose between; `AppShell` owns the page's one. */}
        <div className="min-w-0 flex-1 pb-10">
          <header>
            <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">{title}</h1>
            {description ? (
              <p className="mt-1 text-ink-dim text-sm leading-relaxed">{description}</p>
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
    <nav aria-label="Settings" className="w-full shrink-0 md:w-52">
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
                    <NavItem path={item.path} title={item.title} />
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}

function NavItem({ path, title }: { path: SettingsPath; title: string }) {
  return (
    <Link
      to={path}
      className="-mx-2 block rounded px-2 py-1 text-ink-dim text-sm hover:bg-hover hover:text-ink"
      activeProps={{ className: "bg-raised font-medium text-ink" }}
    >
      {title}
    </Link>
  );
}
