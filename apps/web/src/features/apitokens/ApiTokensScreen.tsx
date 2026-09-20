import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { CopyButton } from "@/shared/components/CopyButton";
import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { useCan } from "@/shared/identity";
import { useProjects } from "@/shared/projects";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import type { ApiTokenCreated, ApiTokenSummary, TokenStatus } from "./api";
import { useApiTokens, useCreateApiToken, useRevokeApiToken } from "./api";
import { ConnectAnAssistant } from "./ConnectAnAssistant";

/**
 * The organization's read tokens (F36): what CI, a script or an AI assistant
 * signs in as.
 *
 * A token is the organization's, not a person's, and the screen says so: the
 * maker is a label, and a token whose maker has left keeps working. What it may
 * read is fixed at creation — one project or all of them — because widening a
 * credential's reach later is how a CI token ends up able to read production.
 *
 * The secret is shown once, here, at the moment of creation. There is no route
 * that can read it back, which is why the panel says so in the API's own words
 * rather than ours.
 */

const EXPIRIES = [
  { id: "30", label: "30 days", days: 30 },
  { id: "90", label: "90 days", days: 90 },
  { id: "365", label: "a year", days: 365 },
] as const;

const EVERY_PROJECT = "all";

const STATUS_TONE: Record<TokenStatus, string> = {
  live: "border-line text-ink-dim",
  revoked: "border-critical-border bg-critical-subtle text-critical-text",
  expired: "border-warning-border bg-warning-subtle text-warning-text",
};

export function ApiTokensScreen() {
  const tokens = useApiTokens();
  // Anyone may read the list — it holds no secret. Minting and withdrawing are
  // an admin's, and the API refuses the rest regardless (F26, BR7).
  const canManage = useCan("manage_api_tokens");
  // Held here and nowhere else: the plaintext is deliberately absent from the
  // query cache, so navigating away is what destroys it.
  const [minted, setMinted] = useState<ApiTokenCreated>();

  return (
    <SettingsLayout
      title="API tokens"
      description="Read your own telemetry from a script, a CI job or an AI assistant. A token belongs to the organization, reads and never writes, and keeps working when the person who made it leaves."
    >
      {minted ? <MintedToken created={minted} onDismiss={() => setMinted(undefined)} /> : null}

      {canManage && minted === undefined ? <CreateTokenForm onCreated={setMinted} /> : null}

      {tokens.isPending ? (
        <SkeletonRows rows={3} label="Loading API tokens" />
      ) : tokens.isError ? (
        <InlineFailure
          title="API tokens could not be loaded"
          error={tokens.error}
          onRetry={() => void tokens.refetch()}
        />
      ) : tokens.data.length === 0 ? (
        <EmptyState
          title="No API tokens yet"
          detail="Nothing outside the console can read this organization's data until one exists. The secret is shown once, at creation, and never again."
        />
      ) : (
        <TokenList tokens={tokens.data} canManage={canManage} />
      )}

      <ConnectAnAssistant />
    </SettingsLayout>
  );
}

function MintedToken({ created, onDismiss }: { created: ApiTokenCreated; onDismiss: () => void }) {
  return (
    <section
      aria-label="New token"
      className="mb-4 border border-brand-line bg-brand-surface px-4 py-3"
    >
      <h2 className="font-medium font-serif text-ink text-lg tracking-tight">
        {created.api_token.name} is ready
      </h2>
      <p className="mt-2 text-ink-dim text-sm leading-relaxed">{created.notice}</p>

      <div className="mt-3 flex items-center gap-3 border border-line bg-surface px-3 py-2">
        <code className="min-w-0 flex-1 select-all break-all font-mono text-ink text-sm">
          {created.token}
        </code>
        <CopyButton value={created.token} label="API token" />
      </div>

      <div className="mt-3">
        <Button variant="secondary" onClick={onDismiss}>
          Done — I have copied it
        </Button>
      </div>
    </section>
  );
}

function CreateTokenForm({ onCreated }: { onCreated: (created: ApiTokenCreated) => void }) {
  const projects = useProjects();
  const [name, setName] = useState("");
  const [scope, setScope] = useState<string>(EVERY_PROJECT);
  const [expiry, setExpiry] = useState<string>("90");
  const create = useCreateApiToken();

  const days = EXPIRIES.find((each) => each.id === expiry)?.days ?? 90;

  return (
    <section aria-label="Create a token" className="mb-6 border border-line px-4 py-3">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!name.trim()) return;
          create.mutate(
            {
              name: name.trim(),
              project_id: scope === EVERY_PROJECT ? null : scope,
              expires_in_days: days,
            },
            {
              onSuccess: (created) => {
                onCreated(created);
                setName("");
              },
            },
          );
        }}
      >
        <div className="min-w-56 flex-1">
          <TextField
            label="Name"
            name="token-name"
            value={name}
            onChange={setName}
            hint="What uses it — “Nightly report”, “CI”. It is what you will revoke by."
          />
        </div>
        <SelectField
          label="Reads"
          value={scope}
          options={[
            { id: EVERY_PROJECT, label: "Every project" },
            ...(projects.data ?? []).map((project) => ({ id: project.id, label: project.name })),
          ]}
          onChange={setScope}
          optionLabel={(option) => option.label}
          capped
        />
        <SelectField
          label="Expires in"
          value={expiry}
          options={EXPIRIES}
          onChange={setExpiry}
          optionLabel={(option) => option.label}
        />
        <div className="pb-1">
          <Button
            type="submit"
            pending={create.isPending}
            pendingLabel="Creating…"
            disabled={!name.trim()}
          >
            Create token
          </Button>
        </div>
      </form>
      {create.error ? <InlineFailure title="No token was created" error={create.error} /> : null}
    </section>
  );
}

function TokenList({ tokens, canManage }: { tokens: ApiTokenSummary[]; canManage: boolean }) {
  const projects = useProjects();
  const revoke = useRevokeApiToken();
  const [confirming, setConfirming] = useState<string>();

  const projectName = (id: string | null) =>
    id === null ? "Every project" : (projects.data?.find((each) => each.id === id)?.name ?? "—");

  return (
    <>
      <ul className="border-line border-t" aria-label="API tokens">
        {tokens.map((token) => (
          <li key={token.id} className="border-line-subtle border-b py-3">
            <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
              <p className="min-w-0 text-ink text-sm">
                <span className="font-medium">{token.name}</span>{" "}
                <code className="font-mono text-2xs text-ink-dim">{token.token_hint}</code>{" "}
                <span
                  className={`ml-1 border px-1.5 py-0.5 text-2xs uppercase tracking-caps ${STATUS_TONE[token.status]}`}
                >
                  {token.status}
                </span>
              </p>
              {canManage && token.status === "live" ? (
                confirming === token.id ? (
                  <span className="flex items-center gap-2">
                    <span className="text-ink-dim text-sm">
                      Anything using it stops on its next request.
                    </span>
                    <Button
                      variant="secondary"
                      pending={revoke.isPending}
                      pendingLabel="Revoking…"
                      onClick={() =>
                        revoke.mutate(token.id, { onSuccess: () => setConfirming(undefined) })
                      }
                    >
                      Revoke it
                    </Button>
                    <Button variant="secondary" onClick={() => setConfirming(undefined)}>
                      Keep it
                    </Button>
                  </span>
                ) : (
                  <Button variant="secondary" onClick={() => setConfirming(token.id)}>
                    Revoke
                  </Button>
                )
              ) : null}
            </div>
            <p className="mt-1 text-ink-dim text-xs">
              Reads {projectName(token.project_id)} · made by{" "}
              {token.created_by_name ?? "someone who has left"} ·{" "}
              {token.status === "revoked" && token.revoked_at
                ? `revoked ${formatRelativeTime(token.revoked_at)}`
                : `expires ${formatTimestamp(token.expires_at)}`}{" "}
              · last used {token.last_used_at ? formatRelativeTime(token.last_used_at) : "never"} ·{" "}
              {token.requests_today} requests today
            </p>
          </li>
        ))}
      </ul>
      {revoke.error ? (
        <InlineFailure title="The token was not revoked" error={revoke.error} />
      ) : null}
    </>
  );
}
