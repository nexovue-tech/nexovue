import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { CopyButton } from "@/shared/components/CopyButton";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { useCan } from "@/shared/identity";
import { useSelection } from "@/shared/selection";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";
import { SETUP_DOCS_PATH } from "@/shared/setup-docs";
import type { ApiKeyCreated, ApiKeySummary } from "./api";
import { useApiKeys, useCreateApiKey, useIngestEndpoint, useRevokeApiKey } from "./api";
import type { KeyTraffic } from "./key-status";
import {
  describeTraffic,
  isVerifiedQuiet,
  keyStatus,
  liveKeyCount,
  orderedKeys,
  suggestedRotationName,
  TRAFFIC_LABEL,
} from "./key-status";

import { SiteKeysSection } from "./SiteKeysSection";

/**
 * The ingest keys of the selected project, and the two ways to replace one.
 *
 * The screen exists because revoking without a create is a trap: a customer whose
 * key leaks would be choosing between leaving it live and stopping their own
 * telemetry for good. So the two actions are separate, named for the situation
 * rather than for the operation, and each says what it costs.
 *
 * **Rotate** is the ordinary path and the one the published guidance describes:
 * mint the replacement, deploy it, watch the old key go quiet, then withdraw it.
 * Both keys work throughout, so nothing goes down.
 *
 * **Revoke now — this key leaked** inverts that on purpose. When the secret is
 * already out, an overlap is time an attacker is using, and security beats a
 * smooth transition. It says plainly that ingest stops until a new key ships.
 *
 * `last_used_at` is what makes the middle step of a rotation a reading rather
 * than a hope, so every row carries it and a key with a day of silence is marked
 * as safe to withdraw.
 */

/* The reading is only as fine as the receiver's credential cache, so a key in
   constant use reads as seconds old rather than as this instant. Said out loud
   rather than left for someone to discover. */
const PRECISION_NOTE =
  "Last use is recorded when a receiver re-reads the key, so it lags live traffic by up to a minute. It is precise enough to tell a key in use from one nothing is sending with.";

const TRAFFIC_TONE: Record<KeyTraffic, string> = {
  "never-used": "border-line text-ink-dim",
  "in-use": "border-info-border bg-info-subtle text-info-text",
  settling: "border-warning-border bg-warning-subtle text-warning-text",
  quiet: "border-line text-ink-dim",
};

export function ApiKeysScreen() {
  const { projectId } = useSelection();
  const keys = useApiKeys(projectId);

  // Held here and nowhere else. The plaintext is deliberately absent from the
  // query cache, so navigating away is what destroys it.
  const [minted, setMinted] = useState<ApiKeyCreated>();
  const [rotating, setRotating] = useState<ApiKeySummary>();

  const create = useCreateApiKey(projectId);
  const revoke = useRevokeApiKey(projectId);
  // Anyone may read the list; minting and withdrawing a key are an admin's (F26, FR1).
  const canManage = useCan("manage_credentials");

  function mint(name: string) {
    create.mutate(name, {
      onSuccess: (created) => {
        setMinted(created);
        setRotating(undefined);
      },
    });
  }

  return (
    <SettingsLayout
      title="Ingest keys"
      description={
        <>
          Two kinds, and they are not interchangeable.{" "}
          <strong className="font-medium text-ink">Server keys</strong> are secret and belong in
          your backend services. <strong className="font-medium text-ink">Browser keys</strong> are
          public and belong in your web pages. Putting a server key in a page hands your
          project&rsquo;s ingest to anyone who views source, so the two are kept apart everywhere —
          including here.
        </>
      }
    >
      <IngestTarget />

      <div className="shrink-0 pt-6 pb-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="font-semibold font-serif text-ink text-lg tracking-tight">Server keys</h2>
          <SecretBadge />
        </div>
        <p className="mt-1 text-ink-dim text-sm leading-relaxed">
          The keys your services authenticate with when they export telemetry. A project can hold
          several at once, which is what lets you replace one without an outage. The secret is shown
          once, at creation, and never again.
        </p>
      </div>

      {minted ? <MintedKey created={minted} onDismiss={() => setMinted(undefined)} /> : null}

      {/* The form stays out of the way while a fresh secret is on screen, unless a
          rotation was started — then both belong, because they are about different
          keys and dismissing the panel first would destroy a secret nothing can
          reissue. */}
      {canManage && (minted === undefined || rotating) ? (
        <CreateKeyForm
          rotating={rotating}
          onCancelRotation={() => setRotating(undefined)}
          onCreate={mint}
          pending={create.isPending}
          error={create.error}
          disabled={projectId === undefined}
        />
      ) : null}

      {projectId === undefined ? (
        <EmptyState
          title="No project selected"
          detail="Keys belong to a project. Choose one in the top bar and its keys appear here."
        />
      ) : keys.isPending ? (
        <div className="">
          <SkeletonRows rows={4} label="Loading API keys" />
        </div>
      ) : keys.isError || keys.data === undefined ? (
        <div className="">
          <InlineFailure
            title="API keys could not be loaded"
            error={keys.error}
            onRetry={() => void keys.refetch()}
          />
        </div>
      ) : keys.data.length === 0 ? (
        <EmptyState
          title="This project has no keys"
          detail="Nothing can send telemetry to this project until one exists. Create one above; the secret is shown once, at that moment, and never again."
        />
      ) : (
        <KeyList
          keys={keys.data}
          canManage={canManage}
          onRotate={setRotating}
          onRevoke={(key) => revoke.mutate(key.id)}
          revokingId={revoke.isPending ? revoke.variables : undefined}
          revokeError={revoke.error}
          onDismissRevokeError={() => revoke.reset()}
        />
      )}

      <SiteKeysSection projectId={projectId} />
    </SettingsLayout>
  );
}

/** The counterpart to SiteKeysSection's Public badge. Every row in this half is
    a secret, and the label says so beside the key rather than only in prose. */
function SecretBadge() {
  return (
    <span className="shrink-0 rounded-sm border border-critical-border bg-critical-subtle px-1.5 py-0.5 font-mono text-2xs text-critical-text uppercase tracking-caps">
      Secret
    </span>
  );
}

/* ------------------------------------------------------------------ minting */

/**
 * The one appearance of the secret. Framed as the reason the storage is safe,
 * because that is what it is — a key we could show twice is a key we would have
 * to keep in a readable form.
 */
function MintedKey({ created, onDismiss }: { created: ApiKeyCreated; onDismiss: () => void }) {
  return (
    <section className="mb-4 border border-brand-line bg-brand-surface px-4 py-3">
      <h2 className="font-medium font-serif text-ink text-lg tracking-tight">
        {created.api_key.name} is ready
      </h2>
      <p className="mt-2 text-ink-dim text-sm leading-relaxed">{created.notice}</p>

      <div className="mt-3 flex items-center gap-3 border border-line bg-surface px-3 py-2">
        <code className="min-w-0 flex-1 select-all break-all font-mono text-ink text-sm">
          {created.key}
        </code>
        <CopyButton value={created.key} label="API key" />
      </div>

      <p className="mt-3 text-ink-dim text-sm leading-relaxed">
        Set it as <code className="font-mono text-2xs text-ink">NEXOVUE_API_KEY</code> in the
        services you want it to carry, deploy, then come back and watch the key you are replacing go
        quiet before you revoke it.
      </p>

      <div className="mt-3">
        <Button variant="secondary" onClick={onDismiss}>
          I have copied it
        </Button>
      </div>
    </section>
  );
}

function CreateKeyForm({
  rotating,
  onCancelRotation,
  onCreate,
  pending,
  error,
  disabled,
}: {
  rotating: ApiKeySummary | undefined;
  onCancelRotation: () => void;
  onCreate: (name: string) => void;
  pending: boolean;
  error: unknown;
  disabled: boolean;
}) {
  const [name, setName] = useState("");
  const suggested = rotating ? suggestedRotationName(rotating, Date.now()) : "";
  const value = name || suggested;

  return (
    <section className="mb-4 border border-line bg-surface px-4 py-3">
      {rotating ? (
        <p className="mb-3 text-ink text-sm leading-relaxed">
          Replacing <span className="font-mono text-2xs">{rotating.key_hint}</span> ({rotating.name}
          ). Both keys work until you revoke the old one, so deploy this replacement first and
          withdraw the old key once it has gone quiet.
        </p>
      ) : null}

      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (value.trim()) onCreate(value.trim());
        }}
      >
        <div className="min-w-64 flex-1">
          <TextField
            label="Name this key"
            name="api-key-name"
            value={value}
            onChange={setName}
            hint="Name it after what carries it, so the list still makes sense in six months."
          />
        </div>
        <div className="flex items-center gap-2 pb-1">
          <Button type="submit" pending={pending} pendingLabel="Creating…" disabled={disabled}>
            {rotating ? "Create replacement" : "Create key"}
          </Button>
          {rotating ? (
            <Button
              variant="secondary"
              onClick={() => {
                setName("");
                onCancelRotation();
              }}
            >
              Cancel
            </Button>
          ) : null}
        </div>
      </form>

      {error ? (
        <div className="mt-3">
          <InlineFailure title="The key could not be created" error={error} />
        </div>
      ) : null}
    </section>
  );
}

/* -------------------------------------------------------------------- the list */

function KeyList({
  keys,
  canManage,
  onRotate,
  onRevoke,
  revokingId,
  revokeError,
  onDismissRevokeError,
}: {
  keys: readonly ApiKeySummary[];
  canManage: boolean;
  onRotate: (key: ApiKeySummary) => void;
  onRevoke: (key: ApiKeySummary) => void;
  revokingId: string | undefined;
  revokeError: unknown;
  onDismissRevokeError: () => void;
}) {
  const now = Date.now();
  const live = liveKeyCount(keys);

  return (
    <section className="shrink-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2 pb-2">
        <p className="text-ink-dim text-sm">
          {live === 1
            ? "One live key. Create a replacement before withdrawing it, or ingest stops."
            : `${live} live keys. Any of them authenticates; withdrawing one leaves the rest working.`}
        </p>
      </div>
      <p className="pb-3 text-ink-dim text-sm leading-relaxed">{PRECISION_NOTE}</p>

      {revokeError ? (
        <div className="pb-3">
          <InlineFailure
            title="The key could not be revoked"
            error={revokeError}
            onRetry={onDismissRevokeError}
          />
        </div>
      ) : null}

      <ul className="border-line border-t">
        {orderedKeys(keys).map((key) => (
          <KeyRow
            canManage={canManage}
            key={key.id}
            apiKey={key}
            now={now}
            onRotate={onRotate}
            onRevoke={onRevoke}
            revoking={revokingId === key.id}
          />
        ))}
      </ul>
    </section>
  );
}

function KeyRow({
  apiKey,
  canManage,
  now,
  onRotate,
  onRevoke,
  revoking,
}: {
  apiKey: ApiKeySummary;
  canManage: boolean;
  now: number;
  onRotate: (key: ApiKeySummary) => void;
  onRevoke: (key: ApiKeySummary) => void;
  revoking: boolean;
}) {
  const status = keyStatus(apiKey, now);
  const [confirmingLeak, setConfirmingLeak] = useState(false);

  return (
    <li className={`border-line-subtle border-b py-3 ${status.revoked ? "opacity-60" : ""}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="font-medium text-ink text-base">{apiKey.name}</span>
            <span className="font-mono text-2xs text-ink-dim">{apiKey.key_hint}</span>
            <SecretBadge />
            {status.revoked ? <RevokedBadge /> : <TrafficBadge traffic={status.traffic} />}
          </div>
          <p className="mt-1 text-ink-dim text-sm leading-relaxed">{describeTraffic(status)}</p>
          <p className="mt-1 font-mono text-2xs text-ink-dim">
            Created {formatTimestamp(apiKey.created_at)}
            {apiKey.last_used_at
              ? ` · last used ${formatRelativeTime(apiKey.last_used_at, now)}`
              : ""}
            {apiKey.revoked_at ? ` · revoked ${formatRelativeTime(apiKey.revoked_at, now)}` : ""}
          </p>
        </div>

        {status.revoked || !canManage ? null : (
          <div className="flex shrink-0 items-center gap-2">
            <Button variant="secondary" onClick={() => onRotate(apiKey)}>
              Rotate
            </Button>
            <Button
              variant="secondary"
              pending={revoking}
              pendingLabel="Revoking…"
              onClick={() => setConfirmingLeak(true)}
            >
              Revoke now — this key leaked
            </Button>
          </div>
        )}
      </div>

      {confirmingLeak ? (
        <LeakConfirmation
          apiKey={apiKey}
          stopsTraffic={!isVerifiedQuiet(status)}
          onConfirm={() => {
            setConfirmingLeak(false);
            onRevoke(apiKey);
          }}
          onCancel={() => setConfirmingLeak(false)}
        />
      ) : null}
    </li>
  );
}

/**
 * The one place the product argues against its own default. Rotating is right
 * almost always; a key whose secret is already public is the exception, and the
 * cost of taking it seriously has to be stated rather than buried in a verb.
 */
function LeakConfirmation({
  apiKey,
  stopsTraffic,
  onConfirm,
  onCancel,
}: {
  apiKey: ApiKeySummary;
  stopsTraffic: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="mt-3 border-critical-solid border-l-2 bg-critical-subtle px-3 py-3">
      <p className="font-medium text-ink text-sm">Revoke {apiKey.name} immediately?</p>
      <p className="mt-1 text-ink-dim text-sm leading-relaxed">
        {stopsTraffic
          ? "This key is still carrying traffic. Revoking it stops those services from sending telemetry within a minute, and it cannot be undone — the replacement for a revoked key is a new key. Do this when the secret is already exposed and every minute it stays live is a minute somebody else can use it."
          : "Nothing is currently sending with this key, so revoking it should stop nothing. It cannot be undone — the replacement for a revoked key is a new key."}
      </p>
      <p className="mt-2 text-ink-dim text-sm leading-relaxed">
        If the key has <em>not</em> leaked, cancel and use Rotate instead: it creates a replacement
        while this one keeps working, so nothing goes down.
      </p>
      <div className="mt-3 flex items-center gap-2">
        <Button onClick={onConfirm}>Yes, revoke it now</Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function TrafficBadge({ traffic }: { traffic: KeyTraffic }) {
  return (
    <span
      className={`shrink-0 rounded-sm border px-1.5 py-0.5 font-mono text-2xs uppercase tracking-caps ${TRAFFIC_TONE[traffic]}`}
    >
      {TRAFFIC_LABEL[traffic]}
    </span>
  );
}

function RevokedBadge() {
  return (
    <span className="shrink-0 rounded-sm border border-critical-border bg-critical-subtle px-1.5 py-0.5 font-mono text-2xs text-critical-text uppercase tracking-caps">
      Revoked
    </span>
  );
}

/**
 * The endpoint the keys on this screen are used against.
 *
 * A key without an endpoint is half an instruction, and this was the missing half: the
 * value appeared once, on the signup handoff, and nowhere else in the product ever
 * again. Rendered as the two lines a customer actually pastes rather than as a fact to
 * transcribe.
 */
function IngestTarget() {
  const { data: ingest } = useIngestEndpoint();
  if (!ingest) return null;

  const endpointLine = `OTEL_EXPORTER_OTLP_ENDPOINT=${ingest.otlp_endpoint}`;
  const headersLine = `OTEL_EXPORTER_OTLP_HEADERS=${ingest.key_header}=<your key>`;

  return (
    <div className="mt-4 border-line border-t pt-3">
      <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Where they are used</p>
      <div className="mt-1.5 flex items-baseline gap-3">
        <code className="min-w-0 flex-1 select-all break-all font-mono text-ink text-xs">
          {endpointLine}
          <br />
          {headersLine}
        </code>
        <CopyButton value={`${endpointLine}\n${headersLine}`} label="both variables" />
      </div>
      <p className="mt-2 text-ink-dim text-xs leading-relaxed">
        Where each one goes, per language and per platform, is in{" "}
        <a
          href={SETUP_DOCS_PATH}
          className="text-brand-text underline underline-offset-4"
          target="_blank"
          rel="noreferrer"
        >
          the setup documentation
        </a>
        .
      </p>
    </div>
  );
}
