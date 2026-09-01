import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { CopyButton } from "@/shared/components/CopyButton";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";

import type { SiteKeySummary } from "./api";
import { useCreateSiteKey, useRevokeSiteKey, useSiteKeys } from "./api";
import type { KeyTraffic } from "./key-status";
import { describeSiteKeyTraffic, keyStatus, orderedKeys, TRAFFIC_LABEL } from "./key-status";

/**
 * The public browser keys of the selected project.
 *
 * This section exists to be *unmistakable* for the secret one above it. A
 * customer who confuses the two either puts a server key in a web page — handing
 * their whole project's ingest to anyone who views source — or spends an
 * afternoon wondering why a browser key is rejected by their backend exporter.
 *
 * So the two are separated by every signal available rather than by a label:
 *
 * - **The key is always on screen, in full.** No hint, no reveal, no "copy it
 *   now". A value that lives in the customer's page source has nothing to hide,
 *   and showing it plainly is the clearest possible statement of that.
 * - **The framing is inverted.** The secret section's copy is about protecting a
 *   secret; this section's says out loud that this key is meant to be public and
 *   what stops it being abused.
 * - **Different actions.** There is no Rotate and no leak confirmation, because
 *   a key that was never secret cannot leak. There is only Retire.
 */

/* Origin is what limits a public key, and it is not a wall. Said here rather
   than left for a customer to assume otherwise from the word "allowed". */
const ORIGIN_NOTE =
  "Requests are checked against this list using the Origin header, which a browser sets and page script cannot forge. It stops other people's websites using your key; it is not a wall against a determined caller, which is why a site key can only ever send browser telemetry to this one project.";

const PUBLIC_NOTE =
  "This key is meant to be public. It ships inside your page, so anyone who views source can read it — that is expected and safe. It can send browser telemetry to this project and do nothing else: it cannot read your data, reach another project, or change anything in this console.";

const ORIGIN_HINT =
  "One per line, as scheme://host — https://app.example.com, http://localhost:3000, or https://*.example.com for subdomains.";

const TRAFFIC_TONE: Record<KeyTraffic, string> = {
  "never-used": "border-line text-ink-dim",
  "in-use": "border-info-border bg-info-subtle text-info-text",
  settling: "border-warning-border bg-warning-subtle text-warning-text",
  quiet: "border-line text-ink-dim",
};

export function SiteKeysSection({ projectId }: { projectId: string | undefined }) {
  const keys = useSiteKeys(projectId);
  const create = useCreateSiteKey(projectId);
  const revoke = useRevokeSiteKey(projectId);

  return (
    <section className="mt-8 shrink-0 border-line border-t pt-5">
      <header className="px-4 pb-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="font-semibold font-serif text-ink text-lg tracking-tight">Browser keys</h2>
          <PublicBadge />
        </div>
        <p className="mt-1 max-w-2xl text-ink-dim text-sm leading-relaxed">{PUBLIC_NOTE}</p>
      </header>

      <CreateSiteKeyForm
        onCreate={(name, allowedOrigins) => create.mutate({ name, allowedOrigins })}
        pending={create.isPending}
        error={create.error}
        disabled={projectId === undefined}
      />

      {projectId === undefined ? null : keys.isPending ? (
        <div className="px-4">
          <SkeletonRows rows={2} label="Loading browser keys" />
        </div>
      ) : keys.isError || keys.data === undefined ? (
        <div className="px-4">
          <InlineFailure
            title="Browser keys could not be loaded"
            error={keys.error}
            onRetry={() => void keys.refetch()}
          />
        </div>
      ) : keys.data.length === 0 ? (
        <EmptyState
          title="This project has no browser keys"
          detail="Create one to send real-user telemetry from a web page. Unlike a server key, it is shown in full here whenever you need it."
        />
      ) : (
        <SiteKeyList
          keys={keys.data}
          onRevoke={(key) => revoke.mutate(key.id)}
          revokingId={revoke.isPending ? revoke.variables : undefined}
          revokeError={revoke.error}
          onDismissRevokeError={() => revoke.reset()}
        />
      )}
    </section>
  );
}

function CreateSiteKeyForm({
  onCreate,
  pending,
  error,
  disabled,
}: {
  onCreate: (name: string, allowedOrigins: string[]) => void;
  pending: boolean;
  error: unknown;
  disabled: boolean;
}) {
  const [name, setName] = useState("");
  const [origins, setOrigins] = useState("");

  // Blank lines dropped so a trailing newline is not an empty origin the API
  // then refuses for reasons the customer cannot see.
  const parsedOrigins = origins
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  const ready = name.trim().length > 0 && parsedOrigins.length > 0;

  return (
    <div className="mx-4 mb-4 border border-line bg-surface px-4 py-3">
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (ready) onCreate(name.trim(), parsedOrigins);
        }}
      >
        <div className="min-w-64 flex-1">
          <TextField
            label="Name this key"
            name="site-key-name"
            value={name}
            onChange={setName}
            hint="Name it after the site that carries it."
          />
        </div>

        <label className="flex flex-col gap-1">
          <span className="font-medium text-ink text-sm">Allowed origins</span>
          <textarea
            name="site-key-origins"
            value={origins}
            onChange={(event) => setOrigins(event.target.value)}
            rows={3}
            spellCheck={false}
            className="border border-line bg-surface px-3 py-2 font-mono text-ink text-sm"
            placeholder="https://app.example.com"
          />
          <span className="text-ink-dim text-xs leading-relaxed">{ORIGIN_HINT}</span>
        </label>

        <div className="flex items-center gap-2">
          <Button type="submit" pending={pending} pendingLabel="Creating…" disabled={disabled}>
            Create browser key
          </Button>
          {ready ? null : (
            <span className="text-ink-dim text-xs">
              A name and at least one origin are required.
            </span>
          )}
        </div>
      </form>

      {error ? (
        <div className="mt-3">
          <InlineFailure title="The browser key could not be created" error={error} />
        </div>
      ) : null}
    </div>
  );
}

function SiteKeyList({
  keys,
  onRevoke,
  revokingId,
  revokeError,
  onDismissRevokeError,
}: {
  keys: readonly SiteKeySummary[];
  onRevoke: (key: SiteKeySummary) => void;
  revokingId: string | undefined;
  revokeError: unknown;
  onDismissRevokeError: () => void;
}) {
  const now = Date.now();

  return (
    <div>
      <p className="max-w-3xl px-4 pb-3 text-ink-dim text-sm leading-relaxed">{ORIGIN_NOTE}</p>

      {revokeError ? (
        <div className="px-4 pb-3">
          <InlineFailure
            title="The browser key could not be retired"
            error={revokeError}
            onRetry={onDismissRevokeError}
          />
        </div>
      ) : null}

      <ul className="border-line border-t">
        {orderedKeys(keys).map((key) => (
          <SiteKeyRow
            key={key.id}
            siteKey={key}
            now={now}
            onRevoke={onRevoke}
            revoking={revokingId === key.id}
          />
        ))}
      </ul>
    </div>
  );
}

function SiteKeyRow({
  siteKey,
  now,
  onRevoke,
  revoking,
}: {
  siteKey: SiteKeySummary;
  now: number;
  onRevoke: (key: SiteKeySummary) => void;
  revoking: boolean;
}) {
  const status = keyStatus(siteKey, now);
  const [confirming, setConfirming] = useState(false);

  return (
    <li className={`border-line-subtle border-b px-4 py-3 ${status.revoked ? "opacity-60" : ""}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="font-medium text-ink text-base">{siteKey.name}</span>
            {status.revoked ? <RetiredBadge /> : <PublicBadge />}
            {status.revoked ? null : <TrafficBadge traffic={status.traffic} />}
          </div>

          {/* The whole key, on every row, always. There is nothing to withhold,
              and hiding it behind a reveal would imply otherwise. */}
          <div className="mt-2 flex items-center gap-3 border border-line bg-raised px-3 py-2">
            <code className="min-w-0 flex-1 select-all break-all font-mono text-ink text-sm">
              {siteKey.public_key}
            </code>
            <CopyButton value={siteKey.public_key} label="browser key" />
          </div>

          <p className="mt-2 max-w-2xl text-ink-dim text-sm leading-relaxed">
            {describeSiteKeyTraffic(status)}
          </p>

          <p className="mt-2 text-ink-dim text-xs leading-relaxed">
            Reports from{" "}
            {siteKey.allowed_origins.length === 0 ? (
              <span className="text-warning-text">
                no origins — this key currently accepts nothing
              </span>
            ) : (
              siteKey.allowed_origins.map((origin, index) => (
                <span key={origin}>
                  {index > 0 ? ", " : ""}
                  <code className="font-mono text-2xs text-ink">{origin}</code>
                </span>
              ))
            )}
          </p>

          <p className="mt-1 font-mono text-2xs text-ink-dim">
            Created {formatTimestamp(siteKey.created_at)}
            {siteKey.last_used_at
              ? ` · last used ${formatRelativeTime(siteKey.last_used_at, now)}`
              : ""}
            {siteKey.revoked_at ? ` · retired ${formatRelativeTime(siteKey.revoked_at, now)}` : ""}
          </p>
        </div>

        {status.revoked ? null : (
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="secondary"
              pending={revoking}
              pendingLabel="Retiring…"
              onClick={() => setConfirming(true)}
            >
              Retire
            </Button>
          </div>
        )}
      </div>

      {confirming ? (
        <div className="mt-3 border-warning-border border-l-2 bg-warning-subtle px-3 py-3">
          <p className="font-medium text-ink text-sm">Retire {siteKey.name}?</p>
          {/* No "this key leaked" wording anywhere: a key that was never secret
              cannot leak, and offering that action would teach the wrong model. */}
          <p className="mt-1 max-w-2xl text-ink-dim text-sm leading-relaxed">
            Pages still carrying this key stop reporting within a minute. It cannot be undone —
            create a new key and redeploy your site to replace it. Retire a browser key when the
            site is gone or has moved to a new key, not because the value was seen: it was always
            visible.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <Button
              onClick={() => {
                setConfirming(false);
                onRevoke(siteKey);
              }}
            >
              Yes, retire it
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
    </li>
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

/** The one badge that must never appear on a secret key, and vice versa. */
function PublicBadge() {
  return (
    <span className="shrink-0 rounded-sm border border-info-border bg-info-subtle px-1.5 py-0.5 font-mono text-2xs text-info-text uppercase tracking-caps">
      Public
    </span>
  );
}

function RetiredBadge() {
  return (
    <span className="shrink-0 rounded-sm border border-line px-1.5 py-0.5 font-mono text-2xs text-ink-dim uppercase tracking-caps">
      Retired
    </span>
  );
}
