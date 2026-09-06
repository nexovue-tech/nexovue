import { FALLBACK_EXCEPTION_TYPE, MUTED_ERROR_PREFIX } from "./constants";
import { guard, reportOnce } from "./diagnostics";

/** One thrown thing, reduced to what the Errors screen needs. */
export interface CapturedError {
  /** Fills exception_type. The Errors screen filters on it being non-empty, so
   * it is never absent — a rejection with no name still gets one. */
  type: string;
  message: string;
  /** Raw and minified. Symbolication happens at query time, not here. */
  stacktrace: string | undefined;
  filePath: string | undefined;
  line: number | undefined;
  column: number | undefined;
}

/**
 * Registers the two listeners that see everything a page throws.
 *
 * window.onerror is deliberately NOT assigned: assigning it replaces whatever
 * the customer's page already had there. addEventListener composes.
 */
export function captureErrors(emit: (error: CapturedError) => void): () => void {
  const onError = guard("errors.error", (event: Event) => {
    // Resource load failures fire at the element and reach window only in the
    // capture phase. They are seen here so they can be excluded: they are not
    // exceptions, they have no exception.type, and reporting them as such would
    // fill the Errors screen with records it cannot render.
    if (isElement(event.target)) return;
    emit(fromErrorEvent(event as ErrorEvent));
  });
  const onRejection = guard("errors.rejection", (event: Event) => {
    emit(fromReason((event as PromiseRejectionEvent).reason));
  });

  // Capture phase, so the resource-load failures above are visible to be excluded.
  window.addEventListener("error", onError, true);
  window.addEventListener("unhandledrejection", onRejection);
  return () => {
    window.removeEventListener("error", onError, true);
    window.removeEventListener("unhandledrejection", onRejection);
  };
}

/**
 * Duck-typed rather than `instanceof Element`: an element inside an iframe
 * belongs to another realm, where instanceof against this realm's Element is
 * false and a resource failure would be reported as an exception.
 */
function isElement(target: EventTarget | null): boolean {
  return target !== null && typeof (target as Partial<Element>).tagName === "string";
}

function fromErrorEvent(event: ErrorEvent): CapturedError {
  if (isMuted(event)) {
    reportOnce(
      "muted-errors",
      "a script served from another origin threw, and the browser stripped the " +
        'error to "Script error." before we could see it. Add crossorigin="anonymous" ' +
        "to that <script src=...> tag and have its origin send Access-Control-Allow-Origin " +
        "for this page.",
    );
  }
  const captured = fromReason(event.error);
  if (captured.stacktrace !== undefined) return captured;
  // No Error object: the ErrorEvent's own location is all there is to point at.
  return {
    ...captured,
    message: captured.message || event.message,
    filePath: event.filename || undefined,
    line: event.lineno || undefined,
    column: event.colno || undefined,
  };
}

/**
 * A rejection reason is frequently not an Error — a string, a plain object, a
 * Response. Reading .stack off one of those is how an error reporter throws
 * inside the handler that was meant to report an error.
 */
function fromReason(reason: unknown): CapturedError {
  if (reason instanceof Error) {
    return {
      type: reason.name || FALLBACK_EXCEPTION_TYPE,
      message: reason.message,
      stacktrace: typeof reason.stack === "string" ? reason.stack : undefined,
      filePath: undefined,
      line: undefined,
      column: undefined,
    };
  }
  return {
    type: constructorName(reason) ?? FALLBACK_EXCEPTION_TYPE,
    message: describe(reason),
    stacktrace: undefined,
    filePath: undefined,
    line: undefined,
    column: undefined,
  };
}

/** The browser replaces a cross-origin script's error with exactly this shape. */
function isMuted(event: ErrorEvent): boolean {
  return (
    (event.error === null || event.error === undefined) &&
    event.message.startsWith(MUTED_ERROR_PREFIX) &&
    !event.filename &&
    !event.lineno
  );
}

function constructorName(value: unknown): string | undefined {
  if (value === null || value === undefined) return undefined;
  try {
    const name = (value as { constructor?: { name?: unknown } }).constructor?.name;
    return typeof name === "string" && name !== "" ? name : undefined;
  } catch {
    return undefined;
  }
}

/**
 * An empty string rather than "null" or "undefined": a message is left unset
 * when there is none, and the ErrorEvent's own message is then free to stand in.
 */
function describe(reason: unknown): string {
  if (reason === null || reason === undefined) return "";
  try {
    if (typeof reason === "string") return reason;
    return JSON.stringify(reason) ?? "";
  } catch {
    return "";
  }
}
