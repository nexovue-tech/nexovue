import { useRouter } from "@tanstack/react-router";
import { useCallback, useSyncExternalStore } from "react";

import type { AssistantSubject } from "@/shared/assistant-context";

/**
 * Starter questions, from the design's table (*Starters*), by the page the panel is open
 * beside. Each is answerable by the catalog and invites evidence, not a cause. A click fills
 * the box and focuses Send, so a mis-tap spends nothing (AC49).
 */
export type StarterPage = "finding" | "service" | "telemetry" | "elsewhere";

export const STARTERS: Readonly<Record<StarterPage, readonly string[]>> = {
  finding: [
    "What changed just before this started?",
    "What do the logs say in this window?",
    "Has this happened before?",
  ],
  service: [
    "What is this service's error rate this hour?",
    "Which endpoint moved most?",
    "What does it depend on?",
  ],
  telemetry: [
    "What are the commonest failures in this window?",
    "Which service do most of these come from?",
    "What should I look at next?",
  ],
  elsewhere: [
    "What is failing right now?",
    "What deployed today?",
    "How did we fix this last time?",
  ],
};

const SERVICE_PAGE = /^\/services\/[^/]+\/?$/;
const TELEMETRY_PAGES = /^\/(traces|logs|errors)(\/|$)/;

/** Which row of the table a page is: its subject first, then its path. */
export function starterPage(subject: AssistantSubject | null, pathname: string): StarterPage {
  if (subject?.findingId) return "finding";
  if (subject?.serviceName || SERVICE_PAGE.test(pathname)) return "service";
  if (TELEMETRY_PAGES.test(pathname)) return "telemetry";
  return "elsewhere";
}

export function startersFor(subject: AssistantSubject | null, pathname: string): readonly string[] {
  return STARTERS[starterPage(subject, pathname)];
}

const noop = () => () => {};

/** The path the panel sits beside, kept current as the person moves; "" outside a router. */
export function usePathname(): string {
  const router = useRouter({ warn: false });
  const subscribe = useCallback(
    (listener: () => void) => (router ? router.history.subscribe(listener) : noop()),
    [router],
  );
  return useSyncExternalStore(subscribe, () => router?.history.location.pathname ?? "");
}
