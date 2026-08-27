import { Menu } from "lucide-react";

import { SelectField } from "@/shared/components/SelectField";
import { ThemeToggle } from "@/shared/theme/ThemeToggle";

import { PROJECTS, TIME_RANGES, useShellControls } from "./shell-controls";

/**
 * Project and time range are the two things that qualify everything on every screen, so
 * they sit above every screen. Both read as sentences rather than as form controls —
 * the whole product is a sentence with evidence under it.
 *
 * Both are local state until the control plane exists (P1-59) and the query layer can
 * key on them.
 */
export function TopBar({
  onOpenCommandPalette,
  onOpenNav,
}: {
  onOpenCommandPalette: () => void;
  onOpenNav: () => void;
}) {
  const { projectId, setProjectId, rangeId, setRangeId } = useShellControls();

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

      <SelectField
        label="Project"
        value={projectId}
        options={PROJECTS}
        onChange={setProjectId}
        optionLabel={(project) => project.name}
      />

      <span aria-hidden className="h-4 w-px bg-line" />

      <SelectField
        label="Last"
        value={rangeId}
        options={TIME_RANGES}
        onChange={setRangeId}
        optionLabel={(range) => range.label}
      />

      <div className="flex-1" />

      <button
        type="button"
        onClick={onOpenCommandPalette}
        className="flex items-baseline gap-2 border border-line px-2 py-0.5 text-ink-dim text-xs hover:border-line-strong hover:text-ink"
      >
        Search
        <kbd className="font-mono text-2xs text-ink-dim">⌘K</kbd>
      </button>

      <ThemeToggle />
    </header>
  );
}
