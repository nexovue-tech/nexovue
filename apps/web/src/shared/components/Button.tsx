import type { ReactNode } from "react";

/**
 * The two weights of action this product has: the one thing the screen is for, and
 * everything else. A third variant would only be a way to avoid deciding which.
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
}: {
  children: ReactNode;
  type?: "button" | "submit";
  variant?: "primary" | "secondary";
  pending?: boolean;
  pendingLabel?: string;
  disabled?: boolean;
  onClick?: () => void;
}) {
  const tone =
    variant === "primary"
      ? "bg-brand text-brand-contrast hover:bg-brand-hover"
      : "border border-line text-ink-dim hover:border-line-strong hover:text-ink";

  return (
    <button
      type={type === "submit" ? "submit" : "button"}
      onClick={onClick}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      className={`rounded-sm px-3 py-1.5 font-medium text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${tone}`}
    >
      {pending && pendingLabel ? pendingLabel : children}
    </button>
  );
}
