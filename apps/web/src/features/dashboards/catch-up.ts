import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "@/shared/api-client";

import type { PanelReadResponse, PanelReadScope, PanelResult } from "./api";
import { readPanels } from "./api";

/**
 * A refused panel reads again by itself (F20, FR45, design section 7).
 *
 * The server refused it for the quota and said how long to wait. After that wait, once,
 * and up to three times, **only the refused panels are read**, on a fixed window and with
 * refresh off too, since that read never finished. While the page is paused or the tab is
 * hidden it waits, and says so; a panel's own "Read again" reads that panel at once.
 *
 * Kept apart from the page's own interval: this one follows the server's wait, not the
 * dashboard's refresh setting.
 */

export const CATCH_UP_TRIES = 3;
const MS_PER_SECOND = 1000;
const DEFAULT_WAIT_SECONDS = 5;

/** The panels the server refused in this answer, and how long it asked the page to wait. */
export function refusedPanels(answer: PanelReadResponse): { ids: string[]; waitSeconds: number } {
  const ids: string[] = [];
  let waitSeconds = 0;
  for (const [panelId, result] of Object.entries(answer.panels)) {
    if (result.state !== "refused") continue;
    ids.push(panelId);
    waitSeconds = Math.max(waitSeconds, result.retry_after_seconds);
  }
  return { ids, waitSeconds: Math.max(waitSeconds, 1) };
}

/** What was read again laid over the answer it follows: the later reading of a panel wins. */
export function mergeCatchUp(
  base: PanelReadResponse,
  patch: Record<string, PanelResult>,
  correlationId: string | null | undefined,
): PanelReadResponse {
  if (Object.keys(patch).length === 0) return base;
  return {
    ...base,
    // A panel still failing quotes the read that last tried it.
    correlation_id: correlationId ?? base.correlation_id,
    panels: { ...base.panels, ...patch },
  };
}

interface CatchUp {
  /** The answer this catch-up was made for: a newer read starts it again. */
  base: PanelReadResponse | undefined;
  patch: Record<string, PanelResult>;
  tries: number;
  correlationId: string | null | undefined;
  /** A whole-request refusal's own wait, which beats the panels' (AC145). */
  waitSeconds: number | undefined;
  /** Bumps with each result, so the page judges its lines once for each. */
  version: number;
}

const FRESH: CatchUp = {
  base: undefined,
  patch: {},
  tries: 0,
  correlationId: undefined,
  waitSeconds: undefined,
  version: 0,
};

/** Whether the tab is on screen: a hidden one waits (design section 7). */
function useTabVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}

export interface PanelCatchUp {
  /** The page's answer, with any panels read again laid over it. */
  answer: PanelReadResponse | undefined;
  /** Whole seconds until the next scheduled read, only while one is scheduled. */
  readingAgainIn: number | undefined;
  /** Counts each catch-up result. */
  version: number;
  /** Reads these panels now, without spending a try. */
  readAgain: (panelIds: string[]) => void;
}

export function usePanelCatchUp(args: {
  base: PanelReadResponse | undefined;
  /** Undefined until the address and the selection agree: nothing is read before. */
  scope: PanelReadScope | undefined;
  paused: boolean;
}): PanelCatchUp {
  const { base, scope, paused } = args;
  const visible = useTabVisible();
  const [state, setState] = useState<CatchUp>(FRESH);
  const [now, setNow] = useState(() => Date.now());
  const [deadline, setDeadline] = useState<number | undefined>();
  const aborts = useRef<AbortController | undefined>(undefined);

  // A newer read of the page starts over: its refusals are its own.
  const current = state.base === base ? state : { ...FRESH, base };
  useEffect(() => {
    if (state.base !== base) setState({ ...FRESH, base });
  }, [base, state.base]);

  const answer = useMemo(
    () =>
      base === undefined ? undefined : mergeCatchUp(base, current.patch, current.correlationId),
    [base, current.patch, current.correlationId],
  );

  const read = useCallback(
    (panelIds: string[], spendsATry: boolean) => {
      if (scope === undefined || panelIds.length === 0) return;
      aborts.current?.abort();
      const controller = new AbortController();
      aborts.current = controller;
      readPanels(scope, panelIds, controller.signal).then(
        (result) => {
          if (controller.signal.aborted) return;
          setState((before) =>
            before.base !== base
              ? before
              : {
                  ...before,
                  patch: { ...before.patch, ...result.panels },
                  correlationId: result.correlation_id,
                  tries: before.tries + (spendsATry ? 1 : 0),
                  waitSeconds: undefined,
                  version: before.version + 1,
                },
          );
        },
        (error: unknown) => {
          if (controller.signal.aborted) return;
          // A refused or failed catch-up is a try spent, and its own wait is the next one.
          const wait = error instanceof ApiError ? error.context.retryAfterSeconds : undefined;
          setState((before) =>
            before.base !== base
              ? before
              : {
                  ...before,
                  tries: before.tries + (spendsATry ? 1 : 0),
                  waitSeconds: wait ?? DEFAULT_WAIT_SECONDS,
                  correlationId:
                    error instanceof ApiError ? error.context.correlationId : before.correlationId,
                  version: before.version + 1,
                },
          );
        },
      );
    },
    [scope, base],
  );

  const refused = useMemo(() => (answer ? refusedPanels(answer) : undefined), [answer]);
  const waiting = paused || !visible;
  const scheduled =
    refused !== undefined &&
    refused.ids.length > 0 &&
    current.tries < CATCH_UP_TRIES &&
    !waiting &&
    scope !== undefined;
  const waitMs = (current.waitSeconds ?? refused?.waitSeconds ?? 0) * MS_PER_SECOND;

  // biome-ignore lint/correctness/useExhaustiveDependencies: a result (version) starts the next wait
  useEffect(() => {
    if (!scheduled || refused === undefined) {
      setDeadline(undefined);
      return;
    }
    const at = Date.now() + waitMs;
    setDeadline(at);
    setNow(Date.now());
    const timer = setTimeout(() => read(refused.ids, true), waitMs);
    return () => clearTimeout(timer);
  }, [scheduled, waitMs, current.version, base, refused?.ids.join(","), read]);

  // The countdown ticks while one is scheduled, and is not announced (design, *Screen readers*).
  useEffect(() => {
    if (deadline === undefined) return;
    const tick = setInterval(() => setNow(Date.now()), MS_PER_SECOND);
    return () => clearInterval(tick);
  }, [deadline]);

  useEffect(() => () => aborts.current?.abort(), []);

  return {
    answer,
    readingAgainIn:
      scheduled && deadline !== undefined
        ? Math.max(0, Math.ceil((deadline - now) / MS_PER_SECOND))
        : undefined,
    version: current.version,
    readAgain: useCallback((panelIds: string[]) => read(panelIds, false), [read]),
  };
}
