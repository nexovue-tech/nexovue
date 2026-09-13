import { Link } from "@tanstack/react-router";

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
        className="flex h-10 items-center border-line border-b px-4 font-semibold font-serif text-ink text-md tracking-tight"
      >
        nexovue
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
    </nav>
  );
}
