import type { ReactNode } from "react";

import { Wordmark } from "@/shared/components/Logo";

/**
 * The frame the bare screens share. Outside the shell on purpose: there is no
 * project, time range or navigation to show to someone who is not signed in yet.
 *
 * Narrow measure, heading in serif, everything else quiet — the same reading register
 * as the rest of the product, just with one thing on the page.
 *
 * `children` is optional because the recovery screens have states with nothing to do
 * on them — a verified address is a sentence and a way onward, not a form.
 */
export function AuthScreenLayout({
  title,
  intro,
  children,
  footer,
}: {
  title: string;
  intro: string;
  children?: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center bg-page px-6 py-12">
      <div className="w-full max-w-sm">
        <Wordmark />

        <h1 className="mt-6 font-semibold font-serif text-2xl text-ink tracking-tight">{title}</h1>
        <p className="mt-2 text-ink-dim text-sm leading-relaxed">{intro}</p>

        {children ? <div className="mt-7">{children}</div> : null}

        {footer ? (
          <div className="mt-6 border-line-subtle border-t pt-4 text-ink-dim text-sm">{footer}</div>
        ) : null}
      </div>
    </div>
  );
}

/** A problem with the submission as a whole, above the fields it could not be pinned to. */
export function FormMessage({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="mb-5 border-critical-solid border-l-2 py-1 pl-3 text-critical-text text-sm"
    >
      {message}
    </p>
  );
}

/**
 * Something that already went right, usually on the screen before this one. `status`
 * rather than `alert`: a screen reader should mention it, not interrupt for it.
 */
export function FormNotice({ message }: { message: string }) {
  return (
    <p role="status" className="mb-5 border-brand-line border-l-2 py-1 pl-3 text-ink text-sm">
      {message}
    </p>
  );
}
