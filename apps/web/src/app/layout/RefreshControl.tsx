import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { Pause, Play } from "lucide-react";
import { useEffect, useState } from "react";

import { SelectField } from "@/shared/components/SelectField";
import type { LiveStream } from "@/shared/refresh";
import {
  NOTHING_LIVE,
  REFRESH_INTERVALS,
  setPaused,
  setRefreshIntervalId,
  useLiveStream,
  useRefreshPreference,
} from "@/shared/refresh";
import { useLiveWindow } from "@/shared/selection";

/**
 * Whether the screen is still moving, how often, and how old what is on it is.
 *
 * **A screen nobody can hold still is a screen nobody can study.** Reading a row
 * carefully, screenshotting it for an incident channel, or turning a laptop towards a
 * colleague all fight a window that advances on its own.
 *
 * One control rather than one per panel: Overview alone draws twelve things, and twelve
 * pause buttons is not twelve times the control.
 */

/** How often the "updated N ago" line re-reads the clock. */
const CLOCK_MS = 1_000;

export function RefreshControl() {
  const preference = useRefreshPreference();
  const live = useLiveWindow();
  const updatedAt = useNewestData();
  const fetching = useIsFetching() > 0;
  const stream = useLiveStream();

  // A screen with nothing live on it shows nothing. An interval over a list of
  // API keys is a control that governs nothing, and reads as a promise that the
  // screen is watching something.
  if (stream === NOTHING_LIVE) return null;

  // A screen that streams has no interval to pick. Its own connection is the
  // refresh, so this shows that instead — in the same place, with the same verb.
  if (stream) return <StreamControl stream={stream} />;

  if (live.isAbsolute) {
    // Not a pause, and it must not read as one: a pinned window is not stale, it is
    // finished. Offering to un-pause something that was never paused would send a
    // reader looking for a control that would do nothing.
    return (
      <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Pinned range</span>
    );
  }

  return (
    <span className="flex items-center gap-3">
      {/* **Paused has to be unmistakable.** A paused screen shows numbers that were
          true a while ago, and during an incident that is how somebody concludes a
          service recovered when it did not. It is a filled warning chip rather than a
          changed icon, and it names the time the numbers are from. Same rule AC18
          states for the findings feed: "we are not looking" must never look like
          "nothing is wrong". */}
      {preference.paused ? (
        <span className="rounded-sm border border-warning-border bg-warning-subtle px-2 py-0.5 font-mono text-2xs text-warning-text uppercase tracking-caps">
          Paused · showing {updatedAt ? updatedAt.toLocaleTimeString() : "—"}
        </span>
      ) : (
        <span className="font-mono text-2xs text-ink-dim tabular-nums">
          {fetching ? "updating…" : describeAge(updatedAt)}
        </span>
      )}

      <button
        type="button"
        onClick={() => setPaused(!preference.paused)}
        aria-pressed={preference.paused}
        aria-label={preference.paused ? "Resume refreshing" : "Pause refreshing"}
        className="flex items-center rounded p-1 text-ink-dim hover:text-ink"
      >
        {preference.paused ? (
          <Play size={14} aria-hidden="true" />
        ) : (
          <Pause size={14} aria-hidden="true" />
        )}
      </button>

      <SelectField
        label="Every"
        value={preference.intervalId}
        options={REFRESH_INTERVALS}
        onChange={setRefreshIntervalId}
        optionLabel={(option) => option.label}
      />
    </span>
  );
}

/** The word for each connection state, in the reader's terms rather than the socket's. */
const STREAM_WORD: Record<LiveStream["state"], string> = {
  idle: "Tail off",
  connecting: "Connecting",
  live: "Live",
  paused: "Paused",
  failed: "Tail ended",
};

/**
 * The same control, for a screen fed by a socket.
 *
 * Paused is the loud state here for the reason it is loud above: a frozen tail
 * during an incident is how somebody concludes a service went quiet when it did
 * not. The difference is that a paused tail is not showing stale numbers, it is
 * holding a position, so it names what resuming does rather than a timestamp.
 */
function StreamControl({ stream }: { stream: LiveStream }) {
  const running = stream.state === "live";

  return (
    <span className="flex items-center gap-3">
      {stream.state === "paused" ? (
        <span className="rounded-sm border border-warning-border bg-warning-subtle px-2 py-0.5 font-mono text-2xs text-warning-text uppercase tracking-caps">
          Paused · new lines are waiting
        </span>
      ) : (
        <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
          {STREAM_WORD[stream.state]}
        </span>
      )}

      <button
        type="button"
        onClick={running ? stream.pause : stream.resume}
        aria-pressed={stream.state === "paused"}
        aria-label={running ? "Pause the live tail" : "Follow the live tail"}
        className="flex items-center rounded p-1 text-ink-dim hover:text-ink"
      >
        {running ? <Pause size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
      </button>
    </span>
  );
}

/**
 * When the freshest answer on screen arrived.
 *
 * The newest across every query rather than any one of them: a reader asking "is this
 * current" means the screen, and the screen is as fresh as its most recent answer.
 */
function useNewestData(): Date | undefined {
  const client = useQueryClient();
  const [newest, setNewest] = useState<number | undefined>();

  useEffect(() => {
    const read = () => {
      const times = client
        .getQueryCache()
        .getAll()
        .map((query) => query.state.dataUpdatedAt)
        .filter((at) => at > 0);
      setNewest(times.length > 0 ? Math.max(...times) : undefined);
    };

    read();
    /**
     * **Read after the render that caused the event, not inside it.**
     *
     * React Query emits `added` from inside `useQuery`'s first render, so a screen
     * mounting a new query made this component set state while that component was
     * still rendering — which React reports as "Cannot update a component while
     * rendering a different component", four times on a page with four new queries.
     * A microtask is the smallest wait that is outside the render: the age on screen
     * is a second coarse anyway, so nothing is lost by reading it a tick later.
     */
    const unsubscribe = client.getQueryCache().subscribe(() => queueMicrotask(read));
    // The cache only fires when something changes; the words below age every second
    // whether or not it does.
    const tick = setInterval(read, CLOCK_MS);
    return () => {
      unsubscribe();
      clearInterval(tick);
    };
  }, [client]);

  return newest === undefined ? undefined : new Date(newest);
}

const MINUTE_SECONDS = 60;

/** Deliberately coarse. A number counting up every second is a distraction, not a fact. */
function describeAge(at: Date | undefined): string {
  if (!at) return "";
  const seconds = Math.max(0, Math.round((Date.now() - at.getTime()) / 1_000));
  if (seconds < 5) return "updated just now";
  if (seconds < MINUTE_SECONDS) return `updated ${seconds}s ago`;
  return `updated ${Math.round(seconds / MINUTE_SECONDS)}m ago`;
}
