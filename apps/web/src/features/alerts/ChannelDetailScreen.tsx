import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { CopyButton } from "@/shared/components/CopyButton";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { useCan } from "@/shared/identity";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import {
  addressesOf,
  describeDestination,
  fieldProblems,
  formatUntil,
  isMuted,
  KIND_NAMES,
  muteUntil,
  SECRET_LABEL,
} from "./alert-words";
import type { ChannelSummary } from "./api";
import {
  useChannel,
  useDeleteChannel,
  useDeliveryLog,
  useMuteChannel,
  useRotateSigningSecret,
  useSendTest,
  useSetQuietHours,
  useUpdateChannel,
} from "./api";

/**
 * One channel: prove it works, fix it, silence it, or remove it (FR3, FR14, FR20,
 * FR23, FR25). The delivery log is here because "did the alert go out?" is asked of
 * a channel, not of a rule.
 */
export function ChannelDetailScreen() {
  const { channelId } = useParams({ from: "/shell/settings/channels/$channelId" });
  const channel = useChannel(channelId);
  // Anyone may see where alerts go and whether they arrived; changing it is an admin's.
  const canManage = useCan("manage_channels");

  return (
    <SettingsLayout
      title={channel.data?.name ?? "Alert channel"}
      description={
        <Link to="/settings/channels" className="text-ink-dim text-sm hover:text-ink">
          All channels
        </Link>
      }
    >
      {channel.isPending ? (
        <SkeletonRows rows={5} label="Loading the channel" />
      ) : channel.isError ? (
        <InlineFailure
          title="The channel could not be loaded"
          error={channel.error}
          onRetry={() => void channel.refetch()}
        />
      ) : (
        <div className="flex max-w-3xl flex-col gap-8">
          <Overview channel={channel.data} />
          {canManage ? <SendTest channel={channel.data} /> : null}
          {canManage && channel.data.kind !== "owners" ? <Settings channel={channel.data} /> : null}
          {canManage ? <Mute channel={channel.data} /> : null}
          {canManage && PEOPLE_READ.has(channel.data.kind) ? (
            <Quiet channel={channel.data} />
          ) : null}
          <DeliveryLog channelId={channel.data.id} />
          {canManage && channel.data.kind !== "owners" ? <Remove channel={channel.data} /> : null}
        </div>
      )}
    </SettingsLayout>
  );
}

function Overview({ channel }: { channel: ChannelSummary }) {
  return (
    <section aria-label="Overview" className="flex flex-col gap-1">
      <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {KIND_NAMES[channel.kind]}
      </p>
      <p className="text-ink text-sm">{describeDestination(channel)}</p>
      {channel.health === "failing" ? (
        <p className="font-medium text-critical-text text-sm">
          Failing: nothing has been delivered since{" "}
          {channel.failing_since ? formatTimestamp(channel.failing_since) : "—"}. The organization's
          owners were emailed. The log below says why.
        </p>
      ) : (
        <p className="text-ink-dim text-sm">
          {channel.last_delivered_at
            ? `Last delivered ${formatRelativeTime(channel.last_delivered_at)}.`
            : "Nothing has been sent here yet."}
        </p>
      )}
      <p className="text-ink-dim text-sm">
        {channel.rules.length === 0
          ? "No rule sends here."
          : `Rules that send here: ${channel.rules.map((rule) => rule.name).join(", ")}.`}
      </p>
    </section>
  );
}

function SendTest({ channel }: { channel: ChannelSummary }) {
  const test = useSendTest();
  const result = test.data;
  return (
    <section aria-labelledby="send-test" className="flex flex-col gap-2">
      <h2 id="send-test" className="font-medium font-serif text-base text-ink">
        Send a test
      </h2>
      <p className="text-ink-dim text-sm">
        A labelled test message, through exactly the path an alert takes.
      </p>
      <div>
        <Button
          variant="secondary"
          pending={test.isPending}
          pendingLabel="Sending…"
          onClick={() => test.mutate(channel.id)}
        >
          Send test
        </Button>
      </div>
      {result ? (
        <p
          role="status"
          className={`text-sm ${result.delivered ? "text-ink" : "text-critical-text"}`}
        >
          {result.pending
            ? "Handed to the delivery workers, which have not answered yet. It may still arrive; look where this channel posts."
            : result.delivered
              ? "Delivered. Look for it where this channel posts."
              : `Not delivered: ${result.reason ?? "no reason was given"}.`}
        </p>
      ) : null}
      {test.error ? <InlineFailure title="The test could not be sent" error={test.error} /> : null}
    </section>
  );
}

function Settings({ channel }: { channel: ChannelSummary }) {
  const update = useUpdateChannel(channel.id);
  const rotate = useRotateSigningSecret(channel.id);
  // What the server last stored, which it may have normalised (trimmed, lowercased):
  // the form compares against that, not against what was typed.
  const [stored, setStored] = useState(channel);
  const [name, setName] = useState(stored.name);
  const [secret, setSecret] = useState("");
  const storedUrl = stored.config.url ?? "";
  const storedAddresses = (stored.config.addresses ?? []).join("\n");
  const [url, setUrl] = useState(storedUrl);
  const [addresses, setAddresses] = useState(storedAddresses);
  const [saved, setSaved] = useState(false);
  const [signingSecret, setSigningSecret] = useState<string>();
  const configChanged = url !== storedUrl || addresses !== storedAddresses;
  const guard = useUnsavedChanges(name !== stored.name || secret !== "" || configChanged);
  const problems = fieldProblems<"name" | "secret" | "config">(update.error, (field) =>
    field === "name" || field === "secret"
      ? field
      : field.startsWith("config")
        ? "config"
        : undefined,
  );
  const config =
    channel.kind === "webhook"
      ? { url: url.trim() }
      : channel.kind === "email"
        ? { addresses: addressesOf(addresses) }
        : undefined;
  const secretLabel = SECRET_LABEL[channel.kind];

  return (
    <section aria-labelledby="channel-settings" className="flex flex-col gap-3">
      <h2 id="channel-settings" className="font-medium font-serif text-base text-ink">
        Settings
      </h2>
      <form
        className="flex max-w-xl flex-col gap-3"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          update.mutate(
            {
              name: name.trim(),
              ...(secret ? { secret: secret.trim() } : {}),
              ...(configChanged && config ? { config } : {}),
            },
            {
              onSuccess: (next) => {
                setStored(next);
                setName(next.name);
                setUrl(next.config.url ?? "");
                setAddresses((next.config.addresses ?? []).join("\n"));
                setSecret("");
                setSaved(true);
              },
            },
          );
        }}
      >
        <TextField
          label="Name"
          name="channel-name"
          value={name}
          onChange={(next) => {
            setName(next);
            setSaved(false);
          }}
          error={problems.name}
        />
        {channel.kind === "webhook" ? (
          <TextField
            label="URL"
            name="channel-url"
            value={url}
            onChange={(next) => {
              setUrl(next);
              setSaved(false);
            }}
            error={problems.config}
          />
        ) : null}
        {channel.kind === "email" ? (
          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
              Addresses
            </span>
            <textarea
              rows={3}
              value={addresses}
              onChange={(event) => {
                setAddresses(event.target.value);
                setSaved(false);
              }}
              className="rounded-sm border border-line bg-surface px-2.5 py-1.5 text-ink text-sm outline-none focus-visible:outline-2 focus-visible:outline-brand"
            />
            {problems.config ? (
              <span className="text-critical-text text-xs">{problems.config}</span>
            ) : null}
          </label>
        ) : null}
        {secretLabel ? (
          <TextField
            label={`Replace the ${secretLabel.toLowerCase()}`}
            name="channel-secret"
            type="password"
            autoComplete="off"
            required={false}
            value={secret}
            onChange={(next) => {
              setSecret(next);
              setSaved(false);
            }}
            error={problems.secret}
            hint={channel.secret_set ? "Set. Leave empty to keep it." : "Not set."}
          />
        ) : null}
        {update.error && Object.keys(problems).length === 0 ? (
          <InlineFailure title="The channel was not saved" error={update.error} />
        ) : null}
        {saved ? (
          <p role="status" className="text-ink text-sm">
            Saved.
          </p>
        ) : null}
        <div>
          <Button type="submit" pending={update.isPending} pendingLabel="Saving…">
            Save
          </Button>
        </div>
        <UnsavedChangesPrompt guard={guard} what="this channel's settings" />
      </form>

      {channel.kind === "webhook" ? (
        <div className="flex flex-col gap-2">
          <p className="text-ink-dim text-sm">
            Replacing the signing secret stops the old one at once: a receiver still checking with
            it will refuse what we send next.
          </p>
          <div>
            <Button
              variant="secondary"
              pending={rotate.isPending}
              onClick={() =>
                rotate.mutate(undefined, {
                  onSuccess: (next) => setSigningSecret(next.signing_secret),
                })
              }
            >
              Replace the signing secret
            </Button>
          </div>
          {signingSecret ? (
            <div className="flex items-center gap-2">
              <code className="select-all break-all rounded-sm border border-line px-2 py-1 font-mono text-ink text-xs">
                {signingSecret}
              </code>
              <CopyButton value={signingSecret} label="Copy the signing secret" />
            </div>
          ) : null}
          {rotate.error ? (
            <InlineFailure title="The signing secret was not replaced" error={rotate.error} />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Mute({ channel }: { channel: ChannelSummary }) {
  const mute = useMuteChannel(channel.id);
  const muted = isMuted(channel.muted_until);
  return (
    <section aria-labelledby="channel-mute" className="flex flex-col gap-2">
      <h2 id="channel-mute" className="font-medium font-serif text-base text-ink">
        Mute
      </h2>
      {muted ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm text-warning-text">
            Muted until {formatUntil(channel.muted_until)}. What would have been sent is in the log,
            marked muted.
          </p>
          <Button variant="secondary" pending={mute.isPending} onClick={() => mute.mutate(null)}>
            Unmute
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {(
            [
              ["1h", "1 hour"],
              ["4h", "4 hours"],
              ["tomorrow", "until 09:00 tomorrow"],
            ] as const
          ).map(([choice, label]) => (
            <Button
              key={choice}
              variant="secondary"
              pending={mute.isPending}
              onClick={() => mute.mutate(muteUntil(choice).toISOString())}
            >
              {label}
            </Button>
          ))}
        </div>
      )}
      {mute.error ? <InlineFailure title="The mute was not changed" error={mute.error} /> : null}
    </section>
  );
}

/* The kinds a person reads; PagerDuty and webhooks keep their own schedules. */
const PEOPLE_READ = new Set(["owners", "email", "slack", "teams", "discord"]);

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

function Quiet({ channel }: { channel: ChannelSummary }) {
  const save = useSetQuietHours(channel.id);
  const stored = channel.quiet_hours;
  const [days, setDays] = useState<number[]>(stored?.days ?? [0, 1, 2, 3, 4]);
  const [start, setStart] = useState(stored?.start.slice(0, 5) ?? "22:00");
  const [end, setEnd] = useState(stored?.end.slice(0, 5) ?? "07:00");
  const timezone = stored?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
  const dirty =
    stored === null
      ? false
      : JSON.stringify([days, start, end]) !==
        JSON.stringify([stored.days, stored.start.slice(0, 5), stored.end.slice(0, 5)]);
  const guard = useUnsavedChanges(dirty);

  return (
    <section aria-labelledby="quiet-hours" className="flex flex-col gap-2">
      <h2 id="quiet-hours" className="font-medium font-serif text-base text-ink">
        Quiet hours
      </h2>
      <p className="text-ink-dim text-sm leading-relaxed">
        Warnings raised during these hours wait, and arrive together when they end. Critical alerts
        are never held. Times are in {timezone}.
      </p>
      <fieldset className="flex flex-wrap gap-3">
        <legend className="sr-only">Days</legend>
        {DAY_NAMES.map((name, day) => (
          <label key={name} className="flex items-center gap-1 text-ink text-sm">
            <input
              type="checkbox"
              checked={days.includes(day)}
              onChange={() =>
                setDays(
                  days.includes(day) ? days.filter((each) => each !== day) : [...days, day].sort(),
                )
              }
            />
            {name}
          </label>
        ))}
      </fieldset>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-2 text-ink-dim">
          From
          <input
            type="time"
            value={start}
            onChange={(event) => setStart(event.target.value)}
            className="rounded-sm border border-line bg-surface px-2 py-1 font-mono text-ink"
          />
        </label>
        <label className="flex items-center gap-2 text-ink-dim">
          to
          <input
            type="time"
            value={end}
            onChange={(event) => setEnd(event.target.value)}
            className="rounded-sm border border-line bg-surface px-2 py-1 font-mono text-ink"
          />
        </label>
      </div>
      <div className="flex gap-2">
        <Button
          variant="secondary"
          pending={save.isPending}
          disabled={days.length === 0}
          onClick={() => save.mutate({ timezone, days, start, end })}
        >
          {stored ? "Save quiet hours" : "Set quiet hours"}
        </Button>
        {stored ? (
          <Button variant="secondary" pending={save.isPending} onClick={() => save.mutate(null)}>
            Remove quiet hours
          </Button>
        ) : null}
      </div>
      {save.error ? <InlineFailure title="Quiet hours were not saved" error={save.error} /> : null}
      <UnsavedChangesPrompt guard={guard} what="the quiet hours" />
    </section>
  );
}

const OUTCOME_TEXT = {
  delivered: "text-ink",
  retrying: "text-warning-text",
  failed: "font-medium text-critical-text",
} as const;

function DeliveryLog({ channelId }: { channelId: string }) {
  const log = useDeliveryLog(channelId);
  return (
    <section aria-labelledby="delivery-log" className="flex flex-col gap-2">
      <h2 id="delivery-log" className="font-medium font-serif text-base text-ink">
        Delivery log
      </h2>
      <p className="text-ink-dim text-sm">Every attempt of the last 30 days, newest first.</p>
      {log.isPending ? (
        <SkeletonRows rows={3} label="Loading the delivery log" />
      ) : log.isError ? (
        <InlineFailure
          title="The delivery log could not be loaded"
          error={log.error}
          onRetry={() => void log.refetch()}
        />
      ) : log.data.length === 0 ? (
        <p className="text-ink-dim text-sm">Nothing has been sent here in the last 30 days.</p>
      ) : (
        <ul className="border-line border-t">
          {log.data.map((attempt) => (
            <li
              key={attempt.id}
              className="flex flex-wrap items-baseline justify-between gap-x-6 border-line-subtle border-b py-2 text-sm"
            >
              <span className="min-w-0">
                <span className={OUTCOME_TEXT[attempt.outcome]}>{attempt.outcome}</span>
                {attempt.http_status !== null ? (
                  <span className="text-ink-dim"> · {attempt.http_status}</span>
                ) : null}
                {attempt.notifications.length > 0 ? (
                  <span className="text-ink-dim"> · {attempt.notifications.join(", ")}</span>
                ) : null}
                {attempt.reason ? (
                  <span className="block text-ink-dim">{attempt.reason}</span>
                ) : null}
              </span>
              <time
                dateTime={attempt.attempted_at}
                className="shrink-0 text-ink-dim tabular-nums"
                title={formatTimestamp(attempt.attempted_at)}
              >
                {formatRelativeTime(attempt.attempted_at)}
              </time>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Remove({ channel }: { channel: ChannelSummary }) {
  const remove = useDeleteChannel();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  return (
    <section aria-labelledby="remove-channel" className="flex flex-col gap-2">
      <h2 id="remove-channel" className="font-medium font-serif text-base text-ink">
        Remove
      </h2>
      {confirming ? (
        <div className="border-critical-solid border-l-2 bg-critical-subtle px-3 py-3">
          <p className="font-medium text-ink text-sm">Remove {channel.name}?</p>
          <p className="mt-1 text-ink-dim text-sm leading-relaxed">
            {channel.rules.length === 0
              ? "No rule sends here."
              : `These rules will stop sending here: ${channel.rules.map((rule) => rule.name).join(", ")}. A rule left with no channel will show as notifying nobody.`}{" "}
            Its delivery log goes with it.
          </p>
          <div className="mt-3 flex gap-2">
            <Button
              pending={remove.isPending}
              pendingLabel="Removing…"
              onClick={() =>
                remove.mutate(channel.id, {
                  onSuccess: () => void navigate({ to: "/settings/channels", ignoreBlocker: true }),
                })
              }
            >
              Yes, remove {channel.name}
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div>
          <Button variant="secondary" onClick={() => setConfirming(true)}>
            Remove this channel
          </Button>
        </div>
      )}
      {remove.error ? (
        <InlineFailure title="The channel was not removed" error={remove.error} />
      ) : null}
    </section>
  );
}
