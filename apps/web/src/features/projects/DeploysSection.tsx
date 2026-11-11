import { useId, useState } from "react";

import { Button } from "@/shared/components/Button";
import { CopyButton } from "@/shared/components/CopyButton";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { Tabs } from "@/shared/components/Tabs";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { shortVersion } from "@/shared/format/version";
import { setupDocUrl } from "@/shared/setup-docs";

import type { DeploySecret, DeploySettings, MintedDeploySecret, RecipeKind } from "./deploys-api";
import {
  MAX_DEPLOY_SECRETS,
  recipe,
  refusalSentence,
  useDeploySettings,
  useMintDeploySecret,
  useWithdrawDeploySecret,
  webhookUrl,
} from "./deploys-api";

/**
 * Project settings, *Deploys* (F28 FR1, design *Project settings*): the URL CI posts
 * to, the secrets that sign for it, the recipes, and what last arrived. Shown only to
 * who may manage credentials (`ProjectSettings`); the API refuses everyone else too.
 *
 * **A secret is on this screen once**, in the answer that minted it, and leaving while
 * it is shown warns (FR2). The list carries endings, never values.
 */
export function DeploysSection({ projectId }: { projectId: string }) {
  const [openedAt] = useState(() => Date.now());
  const settings = useDeploySettings(projectId, openedAt);

  return (
    <section id="deploys" className="flex flex-col gap-5">
      <div className="max-w-2xl">
        <h3 className="font-medium font-serif text-ink text-base tracking-tight">Deploys</h3>
        <p className="mt-1 text-ink-dim text-sm leading-relaxed">
          Your CI tells nexovue when it deploys, signed with a secret from here. Releases then show
          failed and rolled-back deploys, same-version redeploys and commit links beside what your
          traffic shows. Deploys are stored in clear text, as sent; secrets are not.
        </p>
        <p className="mt-2 text-ink-dim text-sm leading-relaxed">
          Already passing a build id into{" "}
          <code className="font-mono text-ink">service.version</code>? Releases appear with no
          webhook. CI adds same-version redeploys, commit links and failed states.{" "}
          <a
            href={setupDocUrl("deploys")}
            className="text-brand-text underline underline-offset-4"
            target="_blank"
            rel="noreferrer"
          >
            How to set up deploys
          </a>
        </p>
      </div>

      {settings.isPending ? (
        <SkeletonRows rows={3} label="Loading deploy settings" />
      ) : settings.isError ? (
        <InlineFailure
          title="The deploy settings could not be loaded"
          error={settings.error}
          onRetry={() => void settings.refetch()}
        />
      ) : (
        <Loaded projectId={projectId} settings={settings.data} />
      )}
    </section>
  );
}

function Loaded({ projectId, settings }: { projectId: string; settings: DeploySettings }) {
  const url = webhookUrl(settings.webhook_path, window.location.origin);
  return (
    <>
      <div className="flex max-w-3xl flex-col gap-1.5">
        <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Webhook URL</p>
        <div className="flex items-center gap-2">
          <code className="min-w-0 select-all break-all font-mono text-ink text-sm">{url}</code>
          <CopyButton value={url} label="Copy the webhook URL" />
        </div>
      </div>
      <Intake settings={settings} />
      <Secrets projectId={projectId} secrets={settings.secrets} />
      <RotationSteps />
      <Recipes url={url} />
      <CurrentVersions settings={settings} />
    </>
  );
}

/** What last arrived, announced as it changes (design: polled, `role=status`). */
function Intake({ settings }: { settings: DeploySettings }) {
  const nothing =
    settings.last_deploy_at === null &&
    settings.last_check_at === null &&
    settings.last_refusal === null;
  return (
    <div role="status" className="max-w-2xl border-line border-l-2 pl-3 text-sm">
      {nothing ? (
        <p className="text-ink-dim">
          Nothing received yet. A wrong signature is not recorded here: your CI&rsquo;s output shows
          a 401.
        </p>
      ) : (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
          <dt className="text-ink-dim">Last deploy received</dt>
          <dd className="text-ink">{when(settings.last_deploy_at)}</dd>
          <dt className="text-ink-dim">Last check received</dt>
          <dd className="text-ink">{when(settings.last_check_at)}</dd>
          <dt className="text-ink-dim">Last refusal</dt>
          <dd className="text-ink">
            {settings.last_refusal === null ? "None" : refusalSentence(settings.last_refusal)}
          </dd>
        </dl>
      )}
      {settings.over_release_cap ? (
        <p className="mt-2 text-ink">
          Older releases are being removed early: this project has over 50,000.
        </p>
      ) : null}
    </div>
  );
}

function when(iso: string | null): string {
  return iso === null ? "Not yet" : formatRelativeTime(iso);
}

function Secrets({ projectId, secrets }: { projectId: string; secrets: DeploySecret[] }) {
  const mint = useMintDeploySecret(projectId);
  const [shown, setShown] = useState<MintedDeploySecret>();
  const atLimit = secrets.length >= MAX_DEPLOY_SECRETS;

  return (
    <div className="flex max-w-3xl flex-col gap-3">
      <p className="font-medium text-ink text-sm">Deploy secrets</p>
      {secrets.length === 0 ? (
        <EmptyState
          headingLevel={4}
          title="This project has no deploy secret"
          detail="Mint one, store it in your CI's secret store as NEXOVUE_DEPLOY_SECRET, and copy a recipe below."
        />
      ) : (
        <ul className="border-line border-t">
          {secrets.map((secret) => (
            <SecretRow key={secret.id} projectId={projectId} secret={secret} />
          ))}
        </ul>
      )}
      {shown ? <MintedSecret secret={shown} onStored={() => setShown(undefined)} /> : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="secondary"
          pending={mint.isPending}
          pendingLabel="Minting…"
          disabled={atLimit}
          onClick={() =>
            mint.mutate(undefined, {
              onSuccess: (answer) => {
                setShown(answer.minted);
                mint.reset();
              },
            })
          }
        >
          Mint a secret
        </Button>
        {atLimit ? (
          <span className="text-ink-dim text-xs">
            A project holds at most {MAX_DEPLOY_SECRETS} live secrets, because every request is
            checked against each one. Withdraw one you no longer use first.
          </span>
        ) : null}
      </div>
      {mint.error ? <InlineFailure title="No secret was minted" error={mint.error} /> : null}
    </div>
  );
}

/** The value, this once. Leaving before saying it is stored warns (FR2). */
function MintedSecret({ secret, onStored }: { secret: MintedDeploySecret; onStored: () => void }) {
  const guard = useUnsavedChanges(true);
  return (
    <div className="border-warning-border border-l-2 bg-warning-subtle px-3 py-3">
      <UnsavedChangesPrompt guard={guard} what="a deploy secret you have not stored yet" />
      <p className="font-medium text-ink text-sm">
        Copy this secret now. It will not be shown again.
      </p>
      <div className="mt-2 flex items-center gap-2">
        <code className="min-w-0 select-all break-all font-mono text-ink text-sm">
          {secret.secret}
        </code>
        <CopyButton value={secret.secret} label="Copy the deploy secret" />
      </div>
      <p className="mt-2 max-w-2xl text-ink-dim text-sm">
        Store it in your CI&rsquo;s secret store as{" "}
        <code className="font-mono text-ink">NEXOVUE_DEPLOY_SECRET</code>. If it is lost, mint
        another.
      </p>
      <div className="mt-3">
        <Button onClick={onStored}>I have stored it</Button>
      </div>
    </div>
  );
}

const DAY_MS = 24 * 60 * 60 * 1_000;

function SecretRow({ projectId, secret }: { projectId: string; secret: DeploySecret }) {
  const withdraw = useWithdrawDeploySecret(projectId);
  const [confirming, setConfirming] = useState(false);
  const [alsoRemove, setAlsoRemove] = useState(false);
  const [since, setSince] = useState(() => localInputValue(Date.now() - DAY_MS));
  const sinceId = useId();
  const lastUse =
    secret.last_used_at === null
      ? "Never used."
      : `Last used ${formatRelativeTime(secret.last_used_at)}.`;

  return (
    <li className="border-line-subtle border-b py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <div className="min-w-0">
          <p className="font-mono text-ink text-sm">…{secret.hint}</p>
          <p className="mt-1 text-ink-dim text-xs">
            <span title={formatTimestamp(secret.created_at)}>
              created {formatRelativeTime(secret.created_at)}
            </span>{" "}
            ·{" "}
            {secret.last_used_at === null
              ? "never used"
              : `used ${formatRelativeTime(secret.last_used_at)}`}
          </p>
        </div>
        {confirming ? null : (
          <Button variant="secondary" onClick={() => setConfirming(true)}>
            Withdraw
          </Button>
        )}
      </div>
      {confirming ? (
        <div className="mt-3 border-critical-solid border-l-2 bg-critical-subtle px-3 py-3">
          <p className="font-medium text-ink text-sm">Withdraw the secret ending {secret.hint}?</p>
          <p className="mt-1 max-w-2xl text-ink-dim text-sm">
            {lastUse} Deploys signed with this secret will be refused.
          </p>
          <label className="mt-3 flex items-center gap-2 text-ink text-sm">
            <input
              type="checkbox"
              checked={alsoRemove}
              onChange={(event) => setAlsoRemove(event.target.checked)}
            />
            Also remove what it sent since
          </label>
          {alsoRemove ? (
            <div className="mt-2 flex flex-col gap-1.5">
              <label
                htmlFor={sinceId}
                className="font-mono text-2xs text-ink-dim uppercase tracking-caps"
              >
                Since, in your browser&rsquo;s time
              </label>
              <input
                id={sinceId}
                type="datetime-local"
                className="w-fit border border-line bg-surface px-2 py-1 font-mono text-ink text-sm"
                value={since}
                onChange={(event) => setSince(event.target.value)}
              />
            </div>
          ) : null}
          <div className="mt-3 flex items-center gap-2">
            <Button
              pending={withdraw.isPending}
              pendingLabel="Withdrawing…"
              onClick={() =>
                withdraw.mutate({
                  secretId: secret.id,
                  removeSince: alsoRemove ? new Date(since).toISOString() : undefined,
                })
              }
            >
              Yes, withdraw it
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
          {withdraw.error ? (
            <div className="mt-3">
              <InlineFailure title="The secret was not withdrawn" error={withdraw.error} />
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

/** A `datetime-local` value for an instant, in the browser's own time. */
function localInputValue(instant: number): string {
  const local = new Date(instant - new Date(instant).getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function RotationSteps() {
  return (
    <div className="max-w-2xl">
      <p className="font-medium text-ink text-sm">Rotating a secret</p>
      <ol className="mt-1 list-decimal pl-5 text-ink-dim text-sm leading-relaxed">
        <li>Mint a new secret here.</li>
        <li>Put it in your CI&rsquo;s secret store in place of the old one.</li>
        <li>Deploy once, and watch &ldquo;used&rdquo; move to the new secret above.</li>
        <li>Withdraw the old secret.</li>
      </ol>
      <p className="mt-1 text-ink-dim text-sm">
        Replacing it in one step would refuse every deploy signed in between, and they would not
        show here.
      </p>
    </div>
  );
}

const RECIPE_TABS: { id: RecipeKind; label: string }[] = [
  { id: "github", label: "GitHub Actions" },
  { id: "gitlab", label: "GitLab CI" },
  { id: "curl", label: "curl" },
];

function Recipes({ url }: { url: string }) {
  const [kind, setKind] = useState<RecipeKind>("github");
  const text = recipe(kind, url);
  return (
    <div className="flex flex-col gap-2">
      <p className="font-medium text-ink text-sm">Send deploys from CI</p>
      <p className="max-w-2xl text-ink-dim text-sm">
        Run it with the dry run first and watch &ldquo;last check received&rdquo; change, then
        remove the dry run. It never fails your deploy: it warns and carries on.
      </p>
      <Tabs
        label="CI recipes"
        items={RECIPE_TABS}
        selected={kind}
        onSelect={(id) => setKind(id as RecipeKind)}
      >
        <div className="flex items-start gap-2">
          <pre className="min-w-0 flex-1 overflow-x-auto border border-line bg-surface p-3 font-mono text-ink text-xs">
            {text}
          </pre>
          <CopyButton value={text} label="Copy the recipe" />
        </div>
      </Tabs>
    </div>
  );
}

/** Each service's version now, so a fixed `latest` that never changes stands out. */
function CurrentVersions({ settings }: { settings: DeploySettings }) {
  if (settings.current_versions.length === 0) return null;
  return (
    <div className="max-w-3xl">
      <p className="font-medium text-ink text-sm">What your services run now</p>
      <ul className="mt-1 text-sm">
        {settings.current_versions.map((service) => (
          <li key={service.service_name} className="flex flex-wrap gap-x-3">
            <span className="text-ink">{service.service_name}</span>
            <code className="font-mono text-ink">{shortVersion(service.version)}</code>
            <span className="text-ink-dim">
              {service.first_seen_at === null
                ? "—"
                : `first seen ${formatRelativeTime(service.first_seen_at)}`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
