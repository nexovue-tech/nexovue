import { Menu } from "lucide-react";

import { SelectField } from "@/shared/components/SelectField";
import { useIsOperator } from "@/shared/identity";
import { useProjectSelection } from "@/shared/projects";
import { setSelectedProjectId } from "@/shared/selection";

import { RefreshControl } from "./RefreshControl";
import { WindowControl } from "./WindowControl";

/**
 * Project and time range are the two things that qualify everything on every screen, so
 * they sit above every screen. Both read as sentences rather than as form controls —
 * the whole product is a sentence with evidence under it.
 *
 * The selections themselves live in shared/, not here: the screens that read them are
 * features, and a feature may not import app/.
 */
export function TopBar({ onOpenNav }: { onOpenNav: () => void }) {
  const { projects, projectId } = useProjectSelection();
  // nexovue's own operator reads no telemetry: their organization holds no
  // project and their screens answer no time range, so both controls would say
  // "—" over every screen they can open
  // (`docs/quality/console-review-2026-09-20.md`).
  const operator = useIsOperator();

  return (
    // One row on a desktop; below it, two — the window gets a row of its own. A tablet
    // needs the second row too: the sidebar is open there and takes the width.
    // A bar that did not wrap pushed the refresh controls, the range picker and "Back
    // to live" off the right edge of a phone, where nobody could reach them.
    <header className="flex min-h-10 shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-line border-b bg-surface px-4 py-1.5 lg:h-10 lg:flex-nowrap lg:py-0">
      {/* The one place an icon beats a word: it sits where every phone user already
          looks for navigation, and the accessible name carries the meaning. */}
      <button
        type="button"
        onClick={onOpenNav}
        aria-label="Open navigation"
        // 44px square on the phone it exists for: at 24 it was the smallest
        // control in the console and the only way to reach another screen
        // (`docs/quality/console-review-2026-09-20.md`).
        className="-ml-2 flex size-11 items-center justify-center rounded text-ink-dim hover:text-ink md:hidden"
      >
        <Menu size={16} aria-hidden="true" />
      </button>

      {operator ? null : projects.length > 0 && projectId ? (
        <SelectField
          capped
          compactLabel
          label="Project"
          value={projectId}
          options={projects}
          onChange={setSelectedProjectId}
          optionLabel={(project) => project.name}
        />
      ) : (
        // Nothing to choose between yet. An empty select reads as a broken control.
        <span className="flex items-center gap-2">
          <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Project</span>
          <span className="text-ink-dim text-sm">—</span>
        </span>
      )}

      {operator ? null : (
        <>
          <span aria-hidden className="hidden h-4 w-px bg-line lg:block" />

          <div className="order-last w-full lg:order-none lg:w-auto">
            <WindowControl />
          </div>
        </>
      )}

      <div className="flex-1" />

      {/* The theme used to sit here, beside the two controls that qualify every
          reading on every screen. It was the only one that changed nothing about
          the data, and it took space on every screen for a decision most people
          make once — it is a row under Settings → General now.

          Search is not here yet, and ⌘K stays reserved for it — no screen may bind
          that chord to something else in the meantime. It carried a button and a
          disabled input until 2026-09-14, which showed a customer who pressed it a
          greyed-out box reading "mounted by P1-63, filled by a later task". An
          internal task id is worse than no button at all. */}

      <RefreshControl />
    </header>
  );
}
