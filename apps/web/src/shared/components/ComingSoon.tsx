/**
 * The placeholder every unbuilt route renders.
 *
 * It names the task that will deliver the screen, so walking the app is also reading
 * the plan — an empty route that says nothing teaches nobody which work is missing.
 */

interface ComingSoonProps {
  title: string;
  /** One line. What this screen will answer, not what it will contain. */
  description: string;
  /** The plan task that delivers it, e.g. "P1-41". */
  taskId: string;
}

export function ComingSoon({ title, description, taskId }: ComingSoonProps) {
  return (
    <section className="mx-auto max-w-2xl px-8 py-16">
      <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Not built yet</p>

      <h1 className="mt-3 font-semibold font-serif text-2xl text-ink tracking-tight">{title}</h1>

      <p className="mt-3 max-w-prose text-ink-dim text-md leading-relaxed">{description}</p>

      <dl className="mt-8 border-line-subtle border-t pt-4 text-sm">
        <div className="flex gap-4 py-1">
          <dt className="w-32 shrink-0 text-ink-dim">Delivered by</dt>
          <dd className="font-mono text-brand-text">{taskId}</dd>
        </div>
      </dl>
    </section>
  );
}
