import type { PropsWithChildren } from "react";
import { useEffect, useId, useRef } from "react";

/**
 * The builder's side sheet (F20, T3.3; design, *The builder*): a native `<dialog>`, so the
 * focus trap, Escape and the inert page behind it are the platform's. `Drawer` is named in
 * the design system and not built (`design-system.md`), so this stays in the feature until
 * a second screen wants one.
 *
 * Mounted only while open: opening takes focus into it, and unmounting gives focus back
 * to whatever opened it, which is where a keyboard user expects to be.
 *
 * It scales with the window, `clamp(28rem, 40vw, 60rem)` (*guess*), so only its sentences
 * are capped, never the sheet (the founder's window rule, AC112). Below `lg` it fills the
 * screen (T3.8).
 */

/** Literal, so Tailwind generates it. Held by `PanelPicker.test.ts` (AC112). */
export const SHEET_WIDTH_CLASS = "lg:w-[clamp(28rem,40vw,60rem)]";

export function SideSheet({
  title,
  onClose,
  children,
}: PropsWithChildren<{
  title: string;
  onClose: () => void;
}>) {
  const dialog = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  // `onClose` is a new function each render; the listener must call today's.
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    const opener = document.activeElement as HTMLElement | null;
    // jsdom has no `showModal`; a browser always does.
    if (typeof element.showModal === "function") element.showModal();
    else element.setAttribute("open", "");
    element.querySelector<HTMLElement>("[data-autofocus]")?.focus();

    // Escape is the platform's `cancel`: close through the page, which owns the state.
    const cancel = (event: Event) => {
      event.preventDefault();
      close.current();
    };
    element.addEventListener("cancel", cancel);
    return () => {
      element.removeEventListener("cancel", cancel);
      if (element.open && typeof element.close === "function") element.close();
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  return (
    <dialog
      ref={dialog}
      aria-labelledby={headingId}
      className={`fixed inset-0 m-0 ml-auto h-full max-h-none w-full max-w-none overflow-y-auto border-line border-l bg-raised p-4 text-ink shadow-overlay backdrop:bg-scrim ${SHEET_WIDTH_CLASS}`}
    >
      <div className="flex flex-col gap-4">
        <header className="flex items-center justify-between gap-3">
          <h2 id={headingId} className="font-semibold text-base text-ink">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="min-h-11 rounded-sm px-2 text-ink-dim text-sm underline hover:text-ink md:min-h-0"
          >
            Close
          </button>
        </header>
        {children}
      </div>
    </dialog>
  );
}
