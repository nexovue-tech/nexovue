import { ApiError } from "../api-client";

/**
 * What a boundary shows. Full screen is reserved for exactly this: the surrounding
 * component tree is gone, so there is nothing smaller left to attach a message to.
 *
 * It always carries an id the user can quote. An error the user cannot describe is an
 * error we cannot find.
 */

interface ErrorScreenProps {
  error: unknown;
  correlationId: string;
  /** Re-render the subtree. Absent when only a reload can help. */
  onRetry?: () => void;
  /** Narrower framing when only one route died rather than the whole shell. */
  scope?: "app" | "route";
}

function describe(error: unknown): { title: string; detail: string } {
  if (error instanceof ApiError) {
    if (error.context.offline) {
      return {
        title: "Cannot reach nexovue",
        detail: "The request never left the network. Check the connection and try again.",
      };
    }
    return {
      title: error.problem?.title ?? "The server could not answer",
      detail: error.problem?.detail ?? `The API replied ${error.status}.`,
    };
  }

  return {
    title: "This screen stopped working",
    detail:
      error instanceof Error && error.message
        ? error.message
        : "Something failed while rendering, and the screen could not recover on its own.",
  };
}

export function ErrorScreen({ error, correlationId, onRetry, scope = "app" }: ErrorScreenProps) {
  const { title, detail } = describe(error);

  return (
    <div className="flex min-h-full items-center justify-center px-8 py-16">
      <div className="w-full max-w-xl border-critical-solid border-l-2 pl-6">
        <p className="font-mono text-2xs text-critical-text uppercase tracking-caps">
          {scope === "app" ? "Application error" : "Screen error"}
        </p>

        <h1 className="mt-3 font-semibold font-serif text-2xl text-ink tracking-tight">{title}</h1>

        <p className="mt-3 text-ink-dim text-md leading-relaxed">{detail}</p>

        <dl className="mt-8 border-line-subtle border-t pt-4 text-sm">
          <div className="flex gap-4 py-1">
            <dt className="w-28 shrink-0 text-ink-dim">Reference</dt>
            <dd className="select-all font-mono text-ink">{correlationId}</dd>
          </div>
        </dl>

        <div className="mt-6 flex gap-3">
          {onRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="rounded-sm bg-brand px-3 py-1.5 font-medium text-brand-contrast text-sm hover:bg-brand-hover"
            >
              Try again
            </button>
          ) : null}
          <a
            href="/services"
            className="rounded-sm border border-line px-3 py-1.5 text-ink-dim text-sm hover:border-line-strong hover:text-ink"
          >
            Back to services
          </a>
        </div>
      </div>
    </div>
  );
}
