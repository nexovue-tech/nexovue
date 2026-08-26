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
export function TopBar({ onOpenCommandPalette }: { onOpenCommandPalette: () => void }) {
  const { projectId, setProjectId, rangeId, setRangeId } = useShellControls();

  return (
    <header className="flex h-10 shrink-0 items-center gap-4 border-line border-b bg-surface px-4">
      <label className="flex items-baseline gap-2">
        <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Project</span>
        <select
          value={projectId}
          onChange={(event) => setProjectId(event.target.value)}
          className="border-none bg-transparent font-medium text-ink text-sm outline-none"
        >
          {PROJECTS.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </select>
      </label>

      <span aria-hidden className="h-4 w-px bg-line" />

      <label className="flex items-baseline gap-2">
        <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Last</span>
        <select
          value={rangeId}
          onChange={(event) => setRangeId(event.target.value)}
          className="border-none bg-transparent font-medium text-ink text-sm outline-none"
        >
          {TIME_RANGES.map((range) => (
            <option key={range.id} value={range.id}>
              {range.label}
            </option>
          ))}
        </select>
      </label>

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
