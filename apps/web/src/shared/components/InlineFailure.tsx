import { ApiError } from "../api-client";
import { Button } from "./Button";

/**
 * A load that failed inside a screen that is otherwise fine.
 *
 * Not a toast — the content is missing, not passing. Not the full-screen boundary —
 * the shell, the navigation and the rest of the screen still work. It carries the
 * correlation id for the same reason ErrorScreen does: an error nobody can quote is an
 * error nobody can find.
 */
export function InlineFailure({
  title,
  error,
  onRetry,
}: {
  title: string;
  error: unknown;
  onRetry?: () => void;
}) {
  const detail =
    error instanceof ApiError
      ? (error.problem?.detail ?? `The API replied ${error.status}.`)
      : "Something failed while loading.";
  const correlationId = error instanceof ApiError ? error.context.correlationId : undefined;

  return (
    <div className="border-critical-solid border-l-2 px-4 py-3">
      <p className="font-medium text-ink text-sm">{title}</p>
      <p className="mt-1 text-ink-dim text-sm">{detail}</p>
      {correlationId ? (
        <p className="mt-2 select-all font-mono text-2xs text-ink-dim">{correlationId}</p>
      ) : null}
      {onRetry ? (
        <div className="mt-3">
          <Button variant="secondary" onClick={onRetry}>
            Try again
          </Button>
        </div>
      ) : null}
    </div>
  );
}
