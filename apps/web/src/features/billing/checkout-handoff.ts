/**
 * Putting the customer in front of the processor's card form.
 *
 * **Nothing here names a processor, and that is the point.** The API answers
 * with a style, a script and an options object; this file loads the script and
 * constructs whatever the script defined, using names the API supplied. A
 * switch of processor is then a change to one adapter on the server, which is
 * what the processor-agnostic port bought and what a hard-coded option name
 * here would spend.
 *
 * The options object is opaque on purpose. Reading a key of it — to prefill a
 * field, to change an amount — is how this file learns whose processor it is,
 * so it is passed along whole and never inspected.
 */

/** A script that is already on the page is not fetched twice. */
const loaded = new Map<string, Promise<void>>();

export type CheckoutHandoff =
  | { style: "hosted_page"; url: string }
  | {
      style: "embedded";
      script_url: string;
      global_name: string;
      options: Record<string, unknown>;
    };

/** What a processor's script is once loaded: something to construct and open. */
interface CheckoutWidget {
  open(): void;
}

type CheckoutConstructor = new (options: Record<string, unknown>) => CheckoutWidget;

export class CheckoutUnavailableError extends Error {}

export const SCRIPT_BLOCKED =
  "The payment provider's checkout could not be loaded. An ad blocker or a blocked network " +
  "request is the usual cause.";

export const SCRIPT_INCOMPLETE =
  "The payment provider's checkout loaded but did not start. Reloading the page usually clears it.";

/**
 * Fetch a script once and resolve when the browser has run it.
 *
 * Rejections are not cached: a blocked request that the customer then unblocks
 * has to be retryable without a reload.
 */
export function loadScript(url: string, document: Document): Promise<void> {
  const already = loaded.get(url);
  if (already) return already;

  const pending = new Promise<void>((resolve, reject) => {
    const tag = document.createElement("script");
    tag.src = url;
    tag.async = true;
    tag.addEventListener("load", () => resolve());
    tag.addEventListener("error", () => {
      loaded.delete(url);
      tag.remove();
      reject(new CheckoutUnavailableError(SCRIPT_BLOCKED));
    });
    document.head.appendChild(tag);
  });

  loaded.set(url, pending);
  return pending;
}

/**
 * The constructor a loaded script left on the window, named by the API.
 *
 * Checked rather than asserted: a script that loads but defines nothing is a
 * blank page for the customer otherwise, at the moment they meant to pay.
 */
export function widgetFrom(
  scope: Record<string, unknown>,
  globalName: string,
): CheckoutConstructor {
  const found = scope[globalName];
  if (typeof found !== "function") throw new CheckoutUnavailableError(SCRIPT_INCOMPLETE);
  return found as CheckoutConstructor;
}

/**
 * Take the customer to the processor, whichever way this one works.
 *
 * A hosted page is a navigation and never returns. An embedded checkout opens
 * over this page and returns the customer to the URL inside `options`, which is
 * the API's own route — so both shapes end on the billing screen.
 */
export async function openCheckout(
  handoff: CheckoutHandoff,
  window: Window & typeof globalThis,
): Promise<void> {
  if (handoff.style === "hosted_page") {
    // Navigated to, never stored: the URL is a bearer credential for exactly
    // one mandate.
    window.location.href = handoff.url;
    return;
  }

  await loadScript(handoff.script_url, window.document);
  const Widget = widgetFrom(window as unknown as Record<string, unknown>, handoff.global_name);
  new Widget(handoff.options).open();
}
