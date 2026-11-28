import type { ReactNode } from "react";

/**
 * Three weights of action (design-system.md, *Buttons*): `primary`, filled, for the one next
 * step in an area, and only one per area; `secondary`, outlined, for other actions; `quiet`,
 * a text button, for small or undoing ones. Every button is 32 px high, 44 px on a phone.
 *
 * `pendingLabel` replaces the label while a submission is in flight — a button that
 * says what is happening beats a button wearing a spinner.
 */
export function Button({
  children,
  type = "button",
  variant = "primary",
  pending = false,
  pendingLabel,
  disabled = false,
  onClick,
  className = "",
  "aria-expanded": expanded,
  "aria-controls": controls,
}: {
  children: ReactNode;
  type?: "button" | "submit";
  variant?: "primary" | "secondary" | "quiet";
  pending?: boolean;
  pendingLabel?: string;
  disabled?: boolean;
  onClick?: () => void;
  /** Extra layout classes, such as full width on a phone (FR46). */
  className?: string;
  /** For a button that shows or hides a region it names. */
  "aria-expanded"?: boolean;
  "aria-controls"?: string;
}) {
  const tone = TONES[variant];

  return (
    <button
      type={type === "submit" ? "submit" : "button"}
      onClick={onClick}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      aria-expanded={expanded}
      aria-controls={controls}
      className={`inline-flex min-h-11 items-center justify-center rounded-sm border px-3 font-medium text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40 md:min-h-8 ${tone} ${className}`}
    >
      {pending && pendingLabel ? pendingLabel : children}
    </button>
  );
}

// Each tone carries a border, transparent where none shows, so every button is the same height.
const TONES = {
  primary:
    "border-brand bg-brand text-brand-contrast hover:border-brand-hover hover:bg-brand-hover",
  secondary: "border-line text-ink hover:border-line-strong hover:bg-hover",
  quiet: "border-transparent px-1! text-brand-text hover:underline underline-offset-2",
} as const;

/**
 * The same look for a link that goes somewhere ("New target" opens a form): it moves, it does
 * not act, so it stays a link, and looks like the button of its weight.
 */
export function buttonClass(variant: keyof typeof TONES = "primary"): string {
  return `inline-flex min-h-11 items-center justify-center rounded-sm border px-3 font-medium text-sm transition-colors md:min-h-8 ${TONES[variant]}`;
}
