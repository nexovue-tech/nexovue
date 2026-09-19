import { setupDocUrl } from "../setup-docs";

/** The setup page every profiling surface points at when there is nothing to show. */
export const PROFILING_SETUP_URL = setupDocUrl("profiling/continuous-profiling");

/**
 * Profiling rests on a preview OpenTelemetry signal, and every surface showing it says
 * so (F17, FR13) — until the signal is declared stable and nexovue supports that version.
 */
export function PreviewLabel() {
  return (
    <span
      className="rounded-sm border border-line px-2 py-0.5 font-mono text-2xs text-ink-dim uppercase tracking-caps"
      title="Profiles use OpenTelemetry's profiles signal, which is still in preview upstream. The format may change as it stabilises."
    >
      Preview
    </span>
  );
}
