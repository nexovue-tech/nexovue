/**
 * The socket, and only the socket. The protocol is `tail-client.ts` and the buffer is
 * `log-feed.ts`; what lives here is the part React owns — when to open one, when to
 * close it, and what a person pressing Pause does to it.
 *
 * **Closing is the whole point of the cleanup.** A tab left open holds a subscription
 * and a server-side poll loop; a tab closed must release both. React's effect teardown
 * runs on unmount and on every change of subscription, and the browser closes the
 * socket when the tab goes — the server treats all three the same way.
 *
 * **Pause is a server-side word.** Buffering on this side while the server keeps
 * sending would only move the problem into the tab. The server holds the lines,
 * discards its oldest when it must, and says how many when the tail resumes.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { LogSearchFilters } from "./api";
import type { LiveBuffer } from "./log-feed";
import { acceptFrame, EMPTY_LIVE } from "./log-feed";
import {
  PAUSE_COMMAND,
  parseTailMessage,
  RESUME_COMMAND,
  retryDelayMs,
  tailUrl,
} from "./tail-client";

export type TailStatus =
  /** No project yet, or the screen asked for the tail to be off. */
  | "idle"
  /** Opening, or reopening after a drop. */
  | "connecting"
  /** Connected and sending. */
  | "live"
  /** Connected, and the server is holding lines rather than sending them. */
  | "paused"
  /** The server ended it and said why. No reconnection follows. */
  | "failed";

export interface LogTail {
  status: TailStatus;
  buffer: LiveBuffer;
  /** Present only when `status` is "failed". */
  failure?: string;
  pause: () => void;
  resume: () => void;
}

export function useLogTail(
  projectId: string | undefined,
  filters: LogSearchFilters,
  enabled: boolean,
): LogTail {
  const [buffer, setBuffer] = useState<LiveBuffer>(EMPTY_LIVE);
  const [status, setStatus] = useState<TailStatus>("idle");
  const [failure, setFailure] = useState<string | undefined>(undefined);

  const socket = useRef<WebSocket | null>(null);
  // A pause has to survive a reconnect, so it is remembered outside the socket.
  const paused = useRef(false);

  // The address *is* the subscription: change a filter and it is a different one.
  const address = useMemo(
    () =>
      projectId === undefined ? undefined : tailUrl(window.location.origin, projectId, filters),
    [projectId, filters],
  );

  useEffect(() => {
    if (!enabled || address === undefined) {
      setStatus("idle");
      return;
    }

    // A new subscription is a new stream; the previous one's lines are not this one's.
    setBuffer(EMPTY_LIVE);
    setFailure(undefined);
    setStatus("connecting");

    let released = false;
    let consecutiveFailures = 0;
    let retry: number | undefined;
    let live: WebSocket | null = null;

    const open = () => {
      live = new WebSocket(address);
      socket.current = live;

      live.onopen = () => {
        consecutiveFailures = 0;
        if (paused.current) live?.send(PAUSE_COMMAND);
        setStatus(paused.current ? "paused" : "live");
      };

      live.onmessage = (event) => {
        const message = parseTailMessage(String(event.data));
        if (message === undefined) return;
        if (message.type === "records") {
          setBuffer((held) => acceptFrame(held, message));
        } else if (message.type === "failed") {
          released = true; // Said why it ended; reconnecting would only repeat it.
          setFailure(message.detail);
          setStatus("failed");
        }
      };

      live.onclose = () => {
        if (released) return;
        consecutiveFailures += 1;
        setStatus("connecting");
        retry = window.setTimeout(open, retryDelayMs(consecutiveFailures));
      };
    };

    open();

    return () => {
      released = true;
      window.clearTimeout(retry);
      socket.current = null;
      live?.close();
    };
  }, [address, enabled]);

  const send = useCallback((command: string) => {
    const open = socket.current;
    if (open !== null && open.readyState === WebSocket.OPEN) open.send(command);
  }, []);

  const pause = useCallback(() => {
    paused.current = true;
    setStatus((current) => (current === "live" ? "paused" : current));
    send(PAUSE_COMMAND);
  }, [send]);

  const resume = useCallback(() => {
    paused.current = false;
    setStatus((current) => (current === "paused" ? "live" : current));
    send(RESUME_COMMAND);
  }, [send]);

  return { status, buffer, failure, pause, resume };
}
