import type { ReactNode } from "react";
import { useEffect, useRef } from "react";

/**
 * An inline confirmation: what an action will do, and its controls (C11; the console
 * has no modal confirm). It takes focus when it opens, so a keyboard or screen-reader
 * user lands on what they just asked for rather than on the button that opened it.
 */
export function ConfirmPanel({ label, children }: { label: string; children: ReactNode }) {
  const panel = useRef<HTMLFieldSetElement>(null);
  useEffect(() => {
    panel.current?.focus();
  }, []);

  return (
    <fieldset
      ref={panel}
      aria-label={label}
      tabIndex={-1}
      className="flex flex-col gap-3 rounded border border-warning-border bg-warning-subtle p-3 text-sm focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
    >
      {children}
    </fieldset>
  );
}
