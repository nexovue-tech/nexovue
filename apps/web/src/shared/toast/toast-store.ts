/**
 * Transient notices. Presentation rule from the design doc: a toast is for something
 * transient the user does not have to act on. Form and field problems go inline; a
 * boundary failure takes the whole screen.
 *
 * Hand-rolled rather than Sonner: Sonner's value is its default look, which is on the
 * banned list. The primitive library (Base UI) is not adopted yet — it is still at
 * 1.0.0-rc.0 — so this stays a plain aria-live region until the primitives task lands.
 */

export type ToastTone = "critical" | "warning" | "info";

export interface Toast {
  id: string;
  tone: ToastTone;
  message: string;
  /** Small mono line under the message: a correlation id, a retry hint. */
  detail?: string;
  /** Milliseconds on screen. Critical notices stay until dismissed. */
  durationMs: number;
}

const DEFAULT_DURATION_MS = 6_000;

let toasts: Toast[] = [];
const listeners = new Set<() => void>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();

function publish() {
  for (const listener of listeners) listener();
}

export function subscribeToToasts(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getToasts(): Toast[] {
  return toasts;
}

export function dismissToast(id: string): void {
  const timer = timers.get(id);
  if (timer) {
    clearTimeout(timer);
    timers.delete(id);
  }
  toasts = toasts.filter((toast) => toast.id !== id);
  publish();
}

export interface ShowToastOptions {
  tone?: ToastTone;
  detail?: string;
  durationMs?: number;
  /** Replaces any existing toast with the same key, so a retry storm shows once. */
  key?: string;
}

export function showToast(message: string, options: ShowToastOptions = {}): string {
  const id = options.key ?? crypto.randomUUID();
  const tone = options.tone ?? "info";
  const durationMs = options.durationMs ?? (tone === "critical" ? 0 : DEFAULT_DURATION_MS);

  if (timers.has(id)) {
    clearTimeout(timers.get(id));
    timers.delete(id);
  }

  const toast: Toast = { id, tone, message, detail: options.detail, durationMs };
  toasts = [...toasts.filter((existing) => existing.id !== id), toast];

  if (durationMs > 0)
    timers.set(
      id,
      setTimeout(() => dismissToast(id), durationMs),
    );
  publish();
  return id;
}
