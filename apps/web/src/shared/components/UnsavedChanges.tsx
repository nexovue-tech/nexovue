import { useBlocker } from "@tanstack/react-router";

import { Button } from "./Button";

/**
 * AC19: a form with unsaved changes warns before the changes are lost.
 *
 * **Each form owns its own guard rather than a screen owning one.** A settings screen is
 * several independent forms — a name, a sampling rule, a masking list — each with its own
 * draft and its own save button. Lifting "is anything dirty" to the screen would make
 * every section's state the screen's business and would warn about the wrong form. The
 * router consults every registered blocker, so one per form composes correctly and each
 * message can name what is actually at risk.
 *
 * **Not `window.confirm`.** A native dialog blocks the page's event loop, cannot carry
 * the form's own words, and is exactly what a person clicks through without reading. This
 * renders as the app, in the app's language.
 *
 * `enableBeforeUnload` extends the same guard to closing the tab or reloading, where the
 * browser's own wording is all that is available and the choice is the browser's to
 * present.
 */

export type UnsavedChangesGuard = ReturnType<typeof useBlocker<never, true>>;

/**
 * Warn before leaving, while `dirty`.
 *
 * `dirty` must mean "a value differs from what is stored", never "the user touched a
 * field". A form that warns after a change was typed and undone teaches people that the
 * warning is noise, and the next one — the real one — gets clicked through too.
 */
export function useUnsavedChanges(dirty: boolean): UnsavedChangesGuard {
  return useBlocker({
    shouldBlockFn: () => dirty,
    enableBeforeUnload: () => dirty,
    withResolver: true,
  }) as UnsavedChangesGuard;
}

interface UnsavedChangesPromptProps {
  guard: UnsavedChangesGuard;
  /** What is about to be lost, in the form's own words: "the masking rules". */
  what: string;
}

/** The dialog, rendered only while a navigation is actually held. */
export function UnsavedChangesPrompt({ guard, what }: UnsavedChangesPromptProps) {
  if (guard.status !== "blocked") return null;

  return (
    // eslint-disable-next-line -- the scrim is decoration; the dialog below takes focus.
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4">
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="unsaved-changes-title"
        className="w-full max-w-md rounded border border-line bg-raised p-5 shadow-lg"
      >
        <h2 id="unsaved-changes-title" className="font-medium font-serif text-base text-ink">
          Leave without saving?
        </h2>
        <p className="mt-2 text-ink-dim text-sm leading-relaxed">
          You have changed {what} and not saved it. Leaving this screen discards the change.
        </p>
        <div className="mt-4 flex gap-2">
          {/* Staying is the safe answer, so it is the one that reads as the default
              action. Leaving is still one click — a guard that is hard to dismiss is a
              guard people learn to route around. */}
          <Button onClick={() => guard.reset()}>Keep editing</Button>
          <Button variant="secondary" onClick={() => guard.proceed()}>
            Discard and leave
          </Button>
        </div>
      </div>
    </div>
  );
}
