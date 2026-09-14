import { Link } from "@tanstack/react-router";

import { ArrowUpRight } from "lucide-react";

import { Wordmark } from "@/shared/components/Logo";
import { SETUP_DOCS_PATH } from "@/shared/setup-docs";
import { ThemeToggle } from "@/shared/theme/ThemeToggle";

import { NAV_SECTIONS } from "../routes/route-manifest";

/**
 * Navigation as an index, not a toolbar. No icons: a list of thirteen icons is thirteen
 * things to decode, and the design doc rules icons-as-the-whole-language out.
 *
 * The active screen is marked by a rule down its left edge and a weight change, so it
 * still reads when the colour does not.
 *
 * Below `md` the sidebar is a drawer rather than a column: at 375px a fixed 13rem rail
 * takes more than half the screen and leaves the content unreadable. It slides over the
 * content instead, and closes when a link is followed.
 */
export function Sidebar({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  return (
    <nav
      aria-label="Screens"
      // `md:translate-x-0` and `md:static` are what make this a plain column again on a
      // wide screen; everything before them only applies to the drawer.
      className={`fixed inset-y-0 left-0 z-40 flex w-52 shrink-0 flex-col border-line border-r bg-surface transition-transform duration-150 md:static md:translate-x-0 motion-reduce:transition-none ${
        open ? "translate-x-0" : "-translate-x-full"
      }`}
    >
      <Link
        to="/"
        onClick={onNavigate}
        className="flex h-10 items-center border-line border-b px-4"
      >
        <Wordmark />
      </Link>

      <div className="flex-1 overflow-y-auto py-3">
        {NAV_SECTIONS.map((section) => (
          <div key={section.label} className="mb-5">
            <h2 className="px-4 pb-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps">
              {section.label}
            </h2>
            <ul>
              {section.items.map((item) => (
                <li key={item.path}>
                  <Link
                    to={item.path}
                    onClick={onNavigate}
                    activeOptions={{ exact: item.path === "/" }}
                    className="block border-transparent border-l-2 py-1 pr-3 pl-3.5 text-ink-dim text-sm hover:bg-hover hover:text-ink"
                    activeProps={{
                      className: "border-brand bg-raised font-medium text-ink",
                    }}
                  >
                    {item.title}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      {/* Below the navigation rather than in it: the documentation is not a screen
          of this console and does not take the time window or the project with it.
          A new tab for the same reason — somebody reading setup instructions is
          working in the console at the same time. */}
      <a
        href={SETUP_DOCS_PATH}
        target="_blank"
        rel="noreferrer"
        className="flex shrink-0 items-center gap-1.5 border-line border-t px-4 py-2.5 text-ink-dim text-sm hover:bg-hover hover:text-ink"
      >
        Setup documentation
        <ArrowUpRight size={12} aria-hidden="true" />
      </a>

      {/* **Reachable on every screen, and not in the top bar.** It lived there until
          2026-09-14 and was moved into Settings on the grounds that a theme is chosen
          once — which is true of a product people open to read something, and false of
          one an operator sits in all day. The room is bright in the morning, dark
          during an incident, and a screenshot pasted into a channel wants whichever
          the reader uses. Two clicks was the wrong price for that.

          Here rather than beside the project and the window, because those two
          qualify every reading on every screen and this qualifies none of them. */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-line border-t px-4 py-2.5">
        <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Theme</span>
        <ThemeToggle />
      </div>
    </nav>
  );
}
