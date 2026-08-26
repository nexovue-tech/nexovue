import { useEffect } from "react";

/**
 * The mount point for the command palette, and the keyboard contract around it.
 *
 * The palette itself is a later task; what has to exist now is the shortcut and the
 * place it opens into, so no screen invents its own ⌘K on the way.
 */

const OPEN_KEY = "k";

export function useCommandPaletteShortcut(onOpen: () => void): void {
  useEffect(() => {
    function handle(event: KeyboardEvent) {
      if (event.key !== OPEN_KEY || !(event.metaKey || event.ctrlKey)) return;
      event.preventDefault();
      onOpen();
    }
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [onOpen]);
}

export function CommandPaletteMount({ open, onClose }: { open: boolean; onClose: () => void }) {
  // Escape is bound to the window, not the panel: it must work wherever focus went.
  useEffect(() => {
    if (!open) return;
    function handle(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      className="fixed inset-0 z-40 flex items-start justify-center bg-page/70 pt-32"
    >
      <button
        type="button"
        aria-label="Close command palette"
        className="absolute inset-0 cursor-default"
        onClick={onClose}
      />

      <div className="relative w-full max-w-2xl border border-line bg-surface shadow-overlay">
        <input
          type="text"
          disabled
          placeholder="Search services, traces and logs — mounted by P1-63, filled by a later task"
          className="w-full bg-transparent px-4 py-3 text-ink text-sm outline-none placeholder:text-ink-dim"
        />
        <p className="border-line border-t px-4 py-2 font-mono text-2xs text-ink-dim">
          esc to close
        </p>
      </div>
    </div>
  );
}
