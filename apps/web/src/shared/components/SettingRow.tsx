import type { ReactNode } from "react";

/**
 * The grammar the account screens are written in.
 *
 * **A label on the left, one control on the right, a hairline between.** The
 * screens used to open with a paragraph and stack a left-aligned form under it,
 * which reads as a document about the settings rather than as the settings: the
 * API keys screen spent four lines of prose before anything a customer could
 * press.
 *
 * The description is deliberately one line and optional. A setting that needs a
 * paragraph to justify it is usually a setting that needs a different name — and
 * where the explanation is genuinely load-bearing, as it is for revoking a leaked
 * key, it belongs in the control's own confirmation rather than above every row a
 * reader scans past.
 */
export function SettingRow({
  label,
  description,
  control,
  children,
}: {
  label: ReactNode;
  /** One line. Not a paragraph — see above. */
  description?: ReactNode;
  /** What acts on this setting, sitting at the end of the row. */
  control?: ReactNode;
  /** Anything that cannot fit the row, drawn under it at full width. */
  children?: ReactNode;
}) {
  return (
    <div className="border-line-subtle border-b py-3 last:border-b-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <div className="min-w-0">
          <p className="text-ink text-sm">{label}</p>
          {description ? (
            <p className="mt-0.5 text-ink-dim text-xs leading-relaxed">{description}</p>
          ) : null}
        </div>
        {control ? <div className="flex shrink-0 items-center gap-2">{control}</div> : null}
      </div>
      {children ? <div className="mt-3">{children}</div> : null}
    </div>
  );
}

/**
 * A named group of rows.
 *
 * The heading and its one line carry what the group is; the rows carry what can
 * be done. A group that cannot say what it is in one line is two groups.
 */
export function SettingGroup({
  title,
  description,
  children,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mt-8 first:mt-0">
      <h2 className="font-medium font-serif text-ink text-lg tracking-tight">{title}</h2>
      {description ? (
        <p className="mt-1 text-ink-dim text-sm leading-relaxed">{description}</p>
      ) : null}
      <div className="mt-3 border-line border-t">{children}</div>
    </section>
  );
}
