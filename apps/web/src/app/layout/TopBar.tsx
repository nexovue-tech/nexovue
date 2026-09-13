import { Menu } from "lucide-react";

import { SelectField } from "@/shared/components/SelectField";
import { useProjectSelection } from "@/shared/projects";
import {
  setSelectedProjectId,
  setSelectedRangeId,
  TIME_RANGES,
  useSelection,
} from "@/shared/selection";
import { ThemeToggle } from "@/shared/theme/ThemeToggle";

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
  const { rangeId } = useSelection();

  return (
    <header className="flex h-10 shrink-0 items-center gap-4 border-line border-b bg-surface px-4">
      {/* The one place an icon beats a word: it sits where every phone user already
          looks for navigation, and the accessible name carries the meaning. */}
      <button
        type="button"
        onClick={onOpenNav}
        aria-label="Open navigation"
        className="-ml-1 rounded p-1 text-ink-dim hover:text-ink md:hidden"
      >
        <Menu size={16} aria-hidden="true" />
      </button>

      {projects.length > 0 && projectId ? (
        <SelectField
          label="Project"
          value={projectId}
          options={projects}
          onChange={setSelectedProjectId}
          optionLabel={(project) => project.name}
        />
      ) : (
        // Nothing to choose between yet. An empty select reads as a broken control.
        <span className="flex items-baseline gap-2">
          <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Project</span>
          <span className="text-ink-dim text-sm">—</span>
        </span>
      )}

      <span aria-hidden className="h-4 w-px bg-line" />

      <SelectField
        label="Last"
        value={rangeId}
        options={TIME_RANGES}
        onChange={setSelectedRangeId}
        optionLabel={(range) => range.label}
      />

      <div className="flex-1" />

      {/* Search is not here yet, and ⌘K stays reserved for it — no screen may bind
          that chord to something else in the meantime. It carried a button and a
          disabled input until 2026-09-14, which showed a customer who pressed it a
          greyed-out box reading "mounted by P1-63, filled by a later task". An
          internal task id is worse than no button at all. */}

      <ThemeToggle />
    </header>
  );
}
