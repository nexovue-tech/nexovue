import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatRelativeTime } from "@/shared/format/time";
import { useCan } from "@/shared/identity";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import { describeDestination, formatUntil, isMuted, KIND_NAMES } from "./alert-words";
import type { ChannelSummary } from "./api";
import { useChannels } from "./api";
import { ChannelForm } from "./ChannelForm";

/**
 * Where this organization's alerts can go (FR23). A channel that has not delivered
 * for an hour is shown failing, above the rest, before anyone has to ask why an
 * alert never arrived (FR21).
 */
export function ChannelsScreen() {
  const channels = useChannels();
  const [connecting, setConnecting] = useState(false);
  const canManage = useCan("manage_channels");
  const [outcome, setOutcome] = useState<string>();

  const ordered = [...(channels.data ?? [])].sort(
    (a, b) => Number(b.health === "failing") - Number(a.health === "failing"),
  );

  return (
    <SettingsLayout
      title="Alert channels"
      description="Where alerts are sent. Every project's rules can use any of them. Secrets — webhook URLs, routing keys — are stored encrypted and never shown again."
    >
      <div className="flex flex-col gap-4">
        {outcome ? (
          <p role="status" className="text-ink text-sm">
            {outcome}
          </p>
        ) : null}
        {connecting ? (
          <ChannelForm
            onCancel={() => setConnecting(false)}
            onCreated={(channel) => {
              setConnecting(false);
              setOutcome(`Connected ${channel.name}. Send a test from its page to be sure.`);
            }}
          />
        ) : canManage ? (
          <div>
            <Button
              onClick={() => {
                setOutcome(undefined);
                setConnecting(true);
              }}
            >
              Connect a channel
            </Button>
          </div>
        ) : null}

        {channels.isPending ? (
          <SkeletonRows rows={3} label="Loading channels" />
        ) : channels.isError ? (
          <InlineFailure
            title="Channels could not be loaded"
            error={channels.error}
            onRetry={() => void channels.refetch()}
          />
        ) : (
          // **The row is the width of the screen; the sentence inside it is not.**
          // Capped, this list stopped two thirds across a wide window and put
          // "Last delivered" in the middle of the page rather than on the right
          // edge every other list in the console uses. The prose inside the row
          // already reads at a measure.
          <ul aria-label="Channels" className="border-line border-t">
            {ordered.map((channel) => (
              <ChannelRow key={channel.id} channel={channel} />
            ))}
          </ul>
        )}
      </div>
    </SettingsLayout>
  );
}

function ChannelRow({ channel }: { channel: ChannelSummary }) {
  const failing = channel.health === "failing";
  return (
    <li
      className={`border-line-subtle border-b border-l-2 py-3 pl-3 ${failing ? "border-l-critical-solid" : "border-l-transparent"}`}
    >
      <Link
        to="/settings/channels/$channelId"
        params={{ channelId: channel.id }}
        className="group flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1"
      >
        <div className="min-w-0 max-w-3xl">
          <p className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium text-base text-ink group-hover:underline">
              {channel.name}
            </span>
            <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
              {KIND_NAMES[channel.kind]}
            </span>
          </p>
          <p className="mt-1 text-ink-dim text-sm">{describeDestination(channel)}</p>
          <p className="mt-1 text-ink-dim text-sm">
            {channel.rules.length === 0
              ? "No rule sends here"
              : `Used by ${channel.rules.length} ${channel.rules.length === 1 ? "rule" : "rules"}`}
          </p>
        </div>
        <span className="shrink-0 text-right text-sm">
          {failing ? (
            <span className="font-medium text-critical-text">
              Failing since{" "}
              {channel.failing_since ? formatRelativeTime(channel.failing_since) : "—"}
            </span>
          ) : isMuted(channel.muted_until) ? (
            <span className="text-warning-text">
              Muted until {formatUntil(channel.muted_until)}
            </span>
          ) : (
            <span className="text-ink-dim">
              {channel.last_delivered_at
                ? `Last delivered ${formatRelativeTime(channel.last_delivered_at)}`
                : "Nothing sent yet"}
            </span>
          )}
        </span>
      </Link>
    </li>
  );
}
