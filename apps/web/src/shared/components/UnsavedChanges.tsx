import { useBlocker } from "@tanstack/react-router";
import { useEffect, useRef } from "react";

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

/** The guard while it is actually holding a navigation, so `reset` and `proceed` exist. */
type BlockedGuard = Extract<UnsavedChangesGuard, { status: "blocked" }>;

/** The dialog, rendered only while a navigation is actually held. */
export function UnsavedChangesPrompt({ guard, what }: UnsavedChangesPromptProps) {
  if (guard.status !== "blocked") return null;
  // Mounted only while blocked, so the dialog's own effects run exactly when it
  // opens and closes — which is what lets it take and give back focus.
  return <BlockedPrompt guard={guard} what={what} />;
}

/**
 * `aria-modal` is a promise to a screen reader that the rest of the page is not
 * there. Keeping that promise is three behaviours, and this had none of them: focus
 * stayed on whatever was clicked, Escape did nothing, and Tab walked the page behind
 * the scrim. A keyboard user met a screen that had simply stopped navigating.
 */
function BlockedPrompt({ guard, what }: { guard: BlockedGuard; what: string }) {
  const dialog = useRef<HTMLDivElement>(null);
  // `reset` is a new function each render; the listeners below must call today's.
  const keep = useRef(guard.reset);
  keep.current = guard.reset;

  useEffect(() => {
    const returnTo = document.activeElement as HTMLElement | null;
    // The first stop in document order, which is "Keep editing" — staying is the safe
    // answer, so the first Enter cannot discard anything. The order of the two buttons
    // is therefore load-bearing, and the test pins it by label.
    const first = () => focusable(dialog.current)[0];
    first()?.focus();

    /**
     * **On the document, not on the dialog.** Handlers bound to the dialog only see
     * keys typed inside it, so one click on the scrim — which is the whole viewport —
     * moved focus to `<body>` and left Escape inert and Tab walking the page behind.
     * That is the defect this is meant to fix, one click away.
     */
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // Escape is "get me out of this dialog", never "discard my work".
        event.preventDefault();
        keep.current();
        return;
      }
      if (event.key !== "Tab") return;

      const stops = focusable(dialog.current);
      if (stops.length === 0) return;
      const inside = dialog.current?.contains(document.activeElement) === true;
      const edge = event.shiftKey ? stops[0] : stops[stops.length - 1];
      if (inside && document.activeElement !== edge) return;
      event.preventDefault();
      (event.shiftKey ? stops[stops.length - 1] : stops[0]).focus();
    };

    /** Whatever the page hands focus to while this is open, the dialog takes back. */
    const pullBack = (event: FocusEvent) => {
      const target = event.target as Node | null;
      if (target && dialog.current?.contains(target)) return;
      first()?.focus();
    };

    document.addEventListener("keydown", keys);
    document.addEventListener("focusin", pullBack);
    return () => {
      document.removeEventListener("keydown", keys);
      document.removeEventListener("focusin", pullBack);
      // The node that had focus may have been unmounted by the navigation this
      // dialog just released; focusing a detached element silently does nothing.
      if (returnTo?.isConnected) returnTo.focus?.();
    };
  }, []);

  return (
    // eslint-disable-next-line -- the scrim is decoration; the dialog below takes focus.
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4">
      <div
        ref={dialog}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="unsaved-changes-title"
        className="w-full max-w-md rounded border border-line bg-raised p-5 shadow-overlay"
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

/** The dialog's own tab stops, in document order. */
function focusable(within: HTMLElement | null): HTMLElement[] {
  if (!within) return [];
  return [
    ...within.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ];
}
