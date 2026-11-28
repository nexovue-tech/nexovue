/**
 * The single place API failures are interpreted.
 *
 * Every query and every mutation in the app funnels through the two caches' global
 * handlers below. A feature that writes `if (error.status === 401)` has duplicated a
 * decision that belongs here, and will get it subtly wrong the second time.
 *
 * The mapping, per the plan:
 *   401  clear the session and go to login, remembering where we were
 *   403  render the forbidden screen (the route boundary does this, not a toast)
 *   429  toast, honouring Retry-After
 *   5xx  toast, and retry with backoff
 *   offline  the shell banner covers it; no toast on top of it
 *
 * One exception, declared by the query rather than guessed here: a query's `meta` may
 * name statuses its own screen says in words, and those raise no toast. The dashboard
 * batch says a 503 and a 429 on the page (F20, AC61, AC145); a toast repeating every
 * refresh on a wall display would be noise over the real message.
 */

import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/shared/api-client";
import { logger } from "@/shared/logger";
import { clearSession, rememberReturnTo } from "@/shared/session";
import { showToast } from "@/shared/toast/toast-store";

/** Failures a screen may say in its own words, named for what they mean, not by status. */
export type OwnWords = "unavailable" | "rate-limited";

/** What a query may declare about its own failures. */
export interface ApiQueryMeta extends Record<string, unknown> {
  /** Failures this query's screen states say itself: no toast for them. */
  ownWords?: readonly OwnWords[];
}

declare module "@tanstack/react-query" {
  interface Register {
    queryMeta: ApiQueryMeta;
  }
}

const STALE_TIME_MS = 30_000;
const MAX_SERVER_ERROR_RETRIES = 2;

const HTTP_UNAUTHORIZED = 401;
const HTTP_FORBIDDEN = 403;
const HTTP_TOO_MANY_REQUESTS = 429;
const HTTP_SERVER_ERROR_FLOOR = 500;
const HTTP_SERVICE_UNAVAILABLE = 503;

const STATUS_OF: Record<OwnWords, number> = {
  unavailable: HTTP_SERVICE_UNAVAILABLE,
  "rate-limited": HTTP_TOO_MANY_REQUESTS,
};

/**
 * The screens where nobody is signed in yet, so a 401 is an answer rather than a lapse.
 * `/invite` asks who is signed in only to warn that accepting will switch accounts; a
 * signed-out invitee is the ordinary case, not an expired session.
 */
const AUTH_SCREEN_PATHS = new Set(["/login", "/signup", "/invite"]);

/** Set by the app once the router exists, so this module does not import the router. */
let redirectToLogin: (() => void) | undefined;

export function setLoginRedirect(redirect: () => void): void {
  redirectToLogin = redirect;
}

function describeWait(seconds: number | undefined): string {
  if (seconds === undefined) return "Try again shortly.";
  if (seconds < 60) return `Try again in ${Math.ceil(seconds)}s.`;
  return `Try again in ${Math.ceil(seconds / 60)} min.`;
}

/** Returns true when the error has been fully dealt with and needs no further UI. */
function handleApiFailure(
  error: unknown,
  operation: string,
  ownWords: readonly OwnWords[] = [],
): void {
  if (!(error instanceof ApiError)) {
    logger.error(`${operation} failed`, error);
    return;
  }

  const { status, context } = error;
  logger.error(`${operation} failed`, error, {
    status,
    trace_id: context.traceId,
    correlation_id: context.correlationId,
  });

  if (context.offline || context.aborted) return; // the shell banner already says this

  if (status === HTTP_UNAUTHORIZED) {
    // A 401 raised by the sign-in form is a wrong password, not an expired session.
    // Bouncing the screen it came from would throw away the message it is about to show.
    if (AUTH_SCREEN_PATHS.has(window.location.pathname)) return;

    clearSession();
    rememberReturnTo(window.location.pathname + window.location.search);
    redirectToLogin?.();
    return;
  }

  // 403 is a property of the screen, not a passing event: the route boundary renders it.
  if (status === HTTP_FORBIDDEN) return;

  // A rate limit or an outage the screen states in its own words (`ApiQueryMeta`).
  if (ownWords.some((words) => STATUS_OF[words] === status)) return;

  if (status === HTTP_TOO_MANY_REQUESTS) {
    showToast("Rate limited by the API.", {
      tone: "warning",
      key: "rate-limited",
      detail: describeWait(context.retryAfterSeconds),
    });
    return;
  }

  if (status >= HTTP_SERVER_ERROR_FLOOR) {
    showToast(error.problem?.title ?? "The server could not answer.", {
      tone: "critical",
      key: `server-error-${status}`,
      detail: context.correlationId,
    });
  }
}

/** Client errors are the caller's fault and will fail again; server errors may not. */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.context.aborted) return false;
  if (error.context.offline) return failureCount < MAX_SERVER_ERROR_RETRIES;
  if (error.status < HTTP_SERVER_ERROR_FLOOR) return false;
  return failureCount < MAX_SERVER_ERROR_RETRIES;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) =>
        handleApiFailure(error, `Query ${String(query.queryKey[0])}`, query.meta?.ownWords),
    }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) =>
        handleApiFailure(
          error,
          `Mutation ${String(mutation.options.mutationKey?.[0] ?? "unnamed")}`,
        ),
    }),
    defaultOptions: {
      queries: {
        staleTime: STALE_TIME_MS,
        retry: shouldRetry,
        // Observability data ages fast; a refocus should show what is true now.
        refetchOnWindowFocus: true,
        throwOnError: (error) => error instanceof ApiError && error.status === HTTP_FORBIDDEN,
      },
      mutations: {
        retry: false, // a write that may have landed must not be replayed blindly
      },
    },
  });
}
