import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { Pause, Play } from "lucide-react";
import { useEffect, useState } from "react";

import { ASSISTANT_QUERY_ROOT } from "@/shared/assistant-context";
import { SelectField } from "@/shared/components/SelectField";
import { formatZonedTime } from "@/shared/format/zoned";
import type { FixedIntervalRefresh, LiveStream } from "@/shared/refresh";
import {
  isFixedInterval,
  NOTHING_LIVE,
  REFRESH_INTERVALS,
  setPaused,
  setRefreshIntervalId,
  TAB_INTERVALS,
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

/**
 * F4 AC75: chat's panel polls a question every 1.5 s, and its answers say nothing about how
 * fresh the screen's readings are, so they move neither the age nor the fetching state.
 */
function isAssistantQuery(key: readonly unknown[]): boolean {
  return key[0] === ASSISTANT_QUERY_ROOT;
}

/** How often the "updated N ago" line re-reads the clock. */
const CLOCK_MS = 1_000;

export function RefreshControl() {
  const preference = useRefreshPreference();
  const live = useLiveWindow();
  const updatedAt = useNewestData();
  const fetching = useIsFetching({ predicate: (query) => !isAssistantQuery(query.queryKey) }) > 0;
  const stream = useLiveStream();

  // A dashboard says what its refresh is, and the control shows that and not the
  // console's own (F20, design *The refresh control*).
  if (isFixedInterval(stream)) return <DashboardRefreshControl refresh={stream} />;

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
      // The window row already says it is pinned, and offers the way back; on a phone
      // there is no room to say it twice.
      <span className="hidden font-mono text-2xs text-ink-dim uppercase tracking-caps sm:inline">
        Pinned range
      </span>
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
        // The age is a comfort, not a warning, so a phone goes without it; the paused
        // chip above is the one that must always show, and it does.
        <span className="hidden font-mono text-2xs text-ink-dim tabular-nums sm:inline">
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
        compactLabel
        label="Every"
        value={preference.intervalId}
        options={REFRESH_INTERVALS}
        onChange={setRefreshIntervalId}
        optionLabel={(option) => option.label}
      />
    </span>
  );
}

/** The 44 px touch target below `md`, and the control's own size above it (FR50). */
const TOUCH_TARGET = "min-h-11 min-w-11 md:min-h-0 md:min-w-0";

/** The dashboard's own setting shown as the one saved choice, when it is not one a tab may pick. */
const OFF_ID = "off";

/**
 * The control while a dashboard is mounted. **The kind is checked in this order: a fixed
 * window first**, or "Every minute" would sit over a page that never refreshes
 * (design, *The refresh control's states*).
 *
 * | State | Shows | Pause? |
 * |---|---|---|
 * | Fixed window | "Fixed time" | no |
 * | Refresh off | "Refresh off · showing 02:14", "Read now" | no |
 * | Paused | "Paused · showing 02:14" | Resume |
 * | Live | the tab's interval, "Read now", the age | yes |
 */
function DashboardRefreshControl({ refresh }: { refresh: FixedIntervalRefresh }) {
  const preference = useRefreshPreference();
  const showing = refresh.lastGoodAt
    ? formatZonedTime(refresh.lastGoodAt, { zone: refresh.zone })
    : "—";

  if (refresh.fixedWindow) {
    return (
      <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Fixed time</span>
    );
  }

  const readNow = (
    <button
      type="button"
      onClick={refresh.readNow}
      className={`flex items-center justify-center rounded px-1 text-ink-dim text-xs underline-offset-2 hover:text-ink hover:underline ${TOUCH_TARGET}`}
    >
      Read now
    </button>
  );

  const interval = (
    <SelectField
      compactLabel
      label="Every"
      value={intervalChoiceOf(refresh)}
      options={intervalChoices(refresh)}
      onChange={(id) =>
        refresh.chooseInterval(TAB_INTERVALS.find((option) => option.id === id)?.ms)
      }
      optionLabel={(option) => option.label}
    />
  );

  if (refresh.intervalMs === false) {
    return (
      <span className="flex items-center gap-3">
        <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
          Refresh off · showing {showing}
        </span>
        {readNow}
        {interval}
      </span>
    );
  }

  return (
    <span className="flex items-center gap-3">
      {preference.paused ? (
        <span className="rounded-sm border border-warning-border bg-warning-subtle px-2 py-0.5 font-mono text-2xs text-warning-text uppercase tracking-caps">
          Paused · showing {showing}
        </span>
      ) : (
        <span className="hidden font-mono text-2xs text-ink-dim tabular-nums sm:inline">
          {refresh.reading
            ? "reading…"
            : describeAge(refresh.lastGoodAt ? new Date(refresh.lastGoodAt) : undefined)}
        </span>
      )}
      {preference.paused ? null : readNow}
      <button
        type="button"
        onClick={() => setPaused(!preference.paused)}
        aria-pressed={preference.paused}
        aria-label={preference.paused ? "Resume refreshing" : "Pause refreshing"}
        className={`flex items-center justify-center rounded p-1 text-ink-dim hover:text-ink ${TOUCH_TARGET}`}
      >
        {preference.paused ? (
          <Play size={14} aria-hidden="true" />
        ) : (
          <Pause size={14} aria-hidden="true" />
        )}
      </button>
      {interval}
    </span>
  );
}

interface IntervalChoice {
  id: string;
  label: string;
  ms: number;
}

/** What this tab may pick: 30 s, 1 minute, 5 minutes, and "Off" where the page's own is off. */
function intervalChoices(refresh: FixedIntervalRefresh): IntervalChoice[] {
  return refresh.savedIntervalMs === false
    ? [{ id: OFF_ID, label: "Off", ms: 0 }, ...TAB_INTERVALS]
    : [...TAB_INTERVALS];
}

function intervalChoiceOf(refresh: FixedIntervalRefresh): string {
  if (refresh.intervalMs === false) return OFF_ID;
  return TAB_INTERVALS.find((option) => option.ms === refresh.intervalMs)?.id ?? OFF_ID;
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
        .filter((query) => !isAssistantQuery(query.queryKey))
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
