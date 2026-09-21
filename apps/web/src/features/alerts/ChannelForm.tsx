import { useId, useState } from "react";

import { Button } from "@/shared/components/Button";
import { CopyButton } from "@/shared/components/CopyButton";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import { TextField } from "@/shared/components/TextField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";

import type { ChannelDraft, ChannelField } from "./alert-words";
import {
  CREATABLE_KINDS,
  channelConfigOf,
  channelProblems,
  emptyChannelDraft,
  fieldProblems,
  KIND_NAMES,
  SECRET_LABEL,
} from "./alert-words";
import type { CreatedChannel } from "./api";
import { useCreateChannel } from "./api";

/**
 * Connect a channel (FR1). One form for every kind: the kind decides which fields it
 * asks for, in the words that kind's own product uses, and each says where to find
 * the value.
 *
 * A secret is typed once and never shown again. A webhook's signing secret is the
 * reverse — nexovue generates it — so it is shown here, once, with the words that
 * say so.
 */

const WHERE_TO_FIND: Partial<Record<ChannelDraft["kind"], string>> = {
  slack:
    "In Slack: create an app with Incoming Webhooks, add one to the channel, and copy its URL.",
  teams:
    "In Teams: add the Workflows template “Post to a channel when a webhook request is received” and copy its URL.",
  discord:
    "In Discord: Server settings → Integrations → Webhooks → New webhook → Copy webhook URL.",
  pagerduty:
    "In PagerDuty: a service's Integrations → Add “Events API V2” → copy the Integration Key.",
  webhook:
    "We POST a signed JSON body to this URL for every notification. The signing secret is shown once, when you save.",
  email: "Every address receives each alert. Separate them with commas or new lines.",
};

const REGIONS: readonly { id: "us" | "eu"; label: string }[] = [
  { id: "us", label: "US (events.pagerduty.com)" },
  { id: "eu", label: "EU (events.eu.pagerduty.com)" },
];

const KINDS = CREATABLE_KINDS.map((kind) => ({ id: kind, label: KIND_NAMES[kind] }));

export function ChannelForm({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: (channel: CreatedChannel) => void;
}) {
  const create = useCreateChannel();
  const [draft, setDraft] = useState<ChannelDraft>(() => emptyChannelDraft());
  const [attempted, setAttempted] = useState(false);
  const [created, setCreated] = useState<CreatedChannel>();
  const addressesId = useId();

  const pristine = JSON.stringify(draft) === JSON.stringify(emptyChannelDraft(draft.kind));
  const guard = useUnsavedChanges(!pristine && created === undefined);

  const server = fieldProblems<ChannelField>(create.error, (field) =>
    field === "secret"
      ? "secret"
      : field === "name"
        ? "name"
        : field.startsWith("config.addresses")
          ? "addresses"
          : field === "config.url"
            ? "url"
            : undefined,
  );
  const problems = { ...server, ...(attempted ? channelProblems(draft) : {}) };
  const unplaced = create.error && Object.keys(server).length === 0 ? create.error : undefined;

  const update = (next: Partial<ChannelDraft>) => {
    setDraft({ ...draft, ...next });
    if (create.error) create.reset();
  };

  if (created?.signing_secret) {
    // Shown once, here, and gone when this closes — it lives in no cache.
    return (
      <div className="flex max-w-xl flex-col gap-3 border border-line bg-surface px-4 py-3">
        <p className="font-medium text-ink text-sm">{created.name} is connected.</p>
        <p className="text-ink-dim text-sm leading-relaxed">
          Verify each request with this signing secret. It is shown now and never again; if it is
          lost, replace it on the channel's page.
        </p>
        <div className="flex items-center gap-2">
          <code className="select-all break-all rounded-sm border border-line px-2 py-1 font-mono text-ink text-xs">
            {created.signing_secret}
          </code>
          <CopyButton value={created.signing_secret} label="Copy the signing secret" />
        </div>
        <div>
          <Button onClick={() => onCreated(created)}>Done</Button>
        </div>
      </div>
    );
  }

  return (
    <form
      className="flex max-w-xl flex-col gap-4 border border-line bg-surface px-4 py-3"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        setAttempted(true);
        if (Object.keys(channelProblems(draft)).length > 0) return;
        create.mutate(
          {
            kind: draft.kind,
            name: draft.name.trim(),
            config: channelConfigOf(draft),
            ...(SECRET_LABEL[draft.kind] !== undefined ? { secret: draft.secret.trim() } : {}),
          },
          {
            onSuccess: (channel) => {
              if (channel.signing_secret) setCreated(channel);
              else onCreated(channel);
            },
          },
        );
      }}
    >
      <SelectField
        stacked
        label="Kind"
        value={draft.kind}
        options={KINDS}
        optionLabel={(option) => option.label}
        onChange={(kind) => update({ kind: kind as ChannelDraft["kind"] })}
      />
      <p className="text-ink-dim text-sm leading-relaxed">{WHERE_TO_FIND[draft.kind]}</p>

      <TextField
        label="Name"
        name="channel-name"
        value={draft.name}
        onChange={(name) => update({ name })}
        error={problems.name}
        hint="How rules and messages refer to it, such as #incidents."
      />

      {SECRET_LABEL[draft.kind] !== undefined ? (
        <TextField
          label={SECRET_LABEL[draft.kind] ?? ""}
          name="channel-secret"
          type="password"
          autoComplete="off"
          value={draft.secret}
          onChange={(secret) => update({ secret })}
          error={problems.secret}
          hint="Stored encrypted and never shown again."
        />
      ) : null}

      {draft.kind === "pagerduty" ? (
        <SelectField
          stacked
          label="Region"
          value={draft.region}
          options={REGIONS}
          optionLabel={(option) => option.label}
          onChange={(region) => update({ region: region as "us" | "eu" })}
        />
      ) : null}

      {draft.kind === "webhook" ? (
        <TextField
          label="URL"
          name="channel-url"
          value={draft.url}
          onChange={(url) => update({ url })}
          error={problems.url}
          hint="https:// only."
        />
      ) : null}

      {draft.kind === "email" ? (
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor={addressesId}
            className="font-mono text-2xs text-ink-dim uppercase tracking-caps"
          >
            Addresses
          </label>
          <textarea
            id={addressesId}
            rows={3}
            value={draft.addresses}
            onChange={(event) => update({ addresses: event.target.value })}
            aria-invalid={problems.addresses ? true : undefined}
            className={`rounded-sm border bg-surface px-2.5 py-1.5 text-ink text-sm outline-none focus-visible:outline-2 focus-visible:outline-brand ${
              problems.addresses ? "border-critical-solid" : "border-line hover:border-line-strong"
            }`}
          />
          {problems.addresses ? (
            <p className="text-critical-text text-xs">{problems.addresses}</p>
          ) : null}
        </div>
      ) : null}

      {unplaced ? <InlineFailure title="The channel was not saved" error={unplaced} /> : null}

      <div className="flex items-center gap-2">
        <Button type="submit" pending={create.isPending} pendingLabel="Connecting…">
          Connect {KIND_NAMES[draft.kind]}
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      <UnsavedChangesPrompt guard={guard} what="this channel" />
    </form>
  );
}
