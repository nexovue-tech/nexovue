import { useSyncExternalStore } from "react";

import { dismissToast, getToasts, subscribeToToasts, type ToastTone } from "./toast-store";

/** Tone is carried by a border rule and a label, never by fill colour alone. */
const TONE_STYLES: Record<ToastTone, { rule: string; label: string; text: string }> = {
  critical: {
    rule: "border-l-critical-solid",
    label: "text-critical-text",
    text: "Failed",
  },
  warning: {
    rule: "border-l-warning-solid",
    label: "text-warning-text",
    text: "Warning",
  },
  info: {
    rule: "border-l-info-solid",
    label: "text-info-text",
    text: "Note",
  },
};

export function ToastRegion() {
  const toasts = useSyncExternalStore(subscribeToToasts, getToasts);

  return (
    <div
      // `polite` so a background failure never interrupts what is being read.
      aria-live="polite"
      aria-relevant="additions"
      className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-88 flex-col gap-2"
    >
      {toasts.map((toast) => {
        const tone = TONE_STYLES[toast.tone];
        return (
          <div
            key={toast.id}
            className={`pointer-events-auto border border-line border-l-2 bg-surface px-3 py-2 shadow-overlay ${tone.rule}`}
          >
            <div className="flex items-baseline gap-2">
              <span className={`font-medium text-2xs uppercase tracking-caps ${tone.label}`}>
                {tone.text}
              </span>
              <p className="flex-1 text-ink text-sm">{toast.message}</p>
              <button
                type="button"
                onClick={() => dismissToast(toast.id)}
                className="text-ink-dim text-xs leading-none hover:text-ink"
                aria-label="Dismiss"
              >
                ✕
              </button>
            </div>
            {toast.detail ? (
              <p className="mt-1 font-mono text-2xs text-ink-dim">{toast.detail}</p>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
