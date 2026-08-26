/**
 * The failures no boundary can see: errors thrown outside React's render path, rejected
 * promises nobody awaited, and — because React 19 stopped re-throwing — anything React
 * hands to window.reportError.
 *
 * These only record. They never change what is on screen: a component that is still
 * working should not be replaced because a timer somewhere threw.
 */

import { logger } from "./logger";

function describe(reason: unknown) {
  return reason instanceof Error ? reason : new Error(String(reason));
}

export function installGlobalHandlers(): void {
  window.addEventListener("error", (event) => {
    // Resource load failures (a font, an image) surface here with no `error` object.
    if (!event.error) {
      logger.warn("Resource failed to load", { source: event.filename });
      return;
    }
    logger.error("Uncaught error", event.error, {
      source: event.filename,
      line: event.lineno,
      column: event.colno,
    });
  });

  window.addEventListener("unhandledrejection", (event) => {
    logger.error("Unhandled promise rejection", describe(event.reason));
  });
}
