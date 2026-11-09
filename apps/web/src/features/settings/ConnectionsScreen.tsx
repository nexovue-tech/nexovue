import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { Refused } from "@/shared/components/Refused";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { useCan, useSession } from "@/shared/identity";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import type { ConnectionPlace, ConnectionTestResult, ConnectionView } from "./api";
import { useConnections, useConnectionTest, useDisconnect, useReplaceAppKey } from "./api";
import {
  ACTION_LABEL,
  DISCONNECT_LEAVES,
  EMAIL_OFF,
  failureFix,
  STATUS_ONLY,
  setupRow,
  TEST_WORKS,
  UNAVAILABLE,
} from "./connection-words";

/** Loaded as a full page, so the browser applies its own form policy (AC73, `Caddyfile`). */
export const GITHUB_START_PATH = "/settings/connections/github/start";

/**
 * Tracker connections (F22, FR28 to FR34): what is connected, whether it works, when it last
 * did, and what to do when it does not. An owner's or admin's screen; the rail hides it from
 * others. Containers use the window; only sentences are capped.
 */
export function ConnectionsScreen() {
  const allowed = useCan("manage_connections");
  const session = useSession();
  const connections = useConnections(allowed);
  // Where connections do not exist, the one sentence is all the screen says (AC81).
  const unavailable = connections.data?.unavailable_reason != null;

  return (
    <SettingsLayout
      title="Connections"
      description={
        unavailable
          ? undefined
          : "GitHub issues for your cases. The first write needs someone's click."
      }
    >
      {!allowed ? (
        session.isPending ? (
          <SkeletonRows rows={3} label="Loading" />
        ) : (
          <Refused role={session.data?.user.role} who="an owner or an admin" />
        )
      ) : connections.isPending ? (
        <SkeletonRows rows={3} label="Loading connections" />
      ) : connections.isError ? (
        <InlineFailure
          title="Connections could not be loaded"
          error={connections.error}
          onRetry={() => void connections.refetch()}
        />
      ) : connections.data.unavailable_reason !== null ? (
        <div data-connections-body>
          <p className="max-w-prose text-ink text-sm">
            {UNAVAILABLE[connections.data.unavailable_reason]}
          </p>
        </div>
      ) : (
        <div data-connections-body className="flex flex-col gap-8">
          <WhatLeaves />
          {connections.data.email_available ? null : (
            <p className="max-w-prose text-ink-dim text-sm">{EMAIL_OFF}</p>
          )}
          <GitHub
            connection={connections.data.connections.find((each) => each.kind === "github")}
          />
        </div>
      )}
    </SettingsLayout>
  );
}

/** The spec's *What leaves this server*, repeated where the choice is made. */
function WhatLeaves() {
  return (
    <section aria-label="What leaves this server" className="flex flex-col gap-2">
      <h2 className="font-medium font-serif text-base">What leaves this server</h2>
      <ul className="flex max-w-prose list-disc flex-col gap-1 pl-5 text-ink-dim text-sm">
        <li>
          An issue, only when someone clicks Create: fixed words, numbers, times, the service name
          in a strict form, and a link to your console. It stays in GitHub for every collaborator to
          read.
        </li>
        <li>Then the issue's open or closed status, and fixed comments, for a linked issue.</li>
        <li>Setting up the App, on github.com.</li>
        <li>
          Coming in: each linked issue's status, read on a timer. A webhook, if GitHub can reach
          this server, only makes that sooner.
        </li>
      </ul>
    </section>
  );
}

function GitHub({ connection }: { connection: ConnectionView | undefined }) {
  if (connection === undefined) return <NotConnected />;
  if (connection.state === "setting_up") return <Setup connection={connection} />;
  return <Connected connection={connection} />;
}

function NotConnected() {
  return (
    <section aria-label="GitHub" className="flex flex-col gap-2">
      <h2 className="font-medium font-serif text-base">GitHub</h2>
      <p className="max-w-prose text-ink-dim text-sm">
        A private GitHub App your organization owns, installed on the repositories you choose, so a
        case can open an issue and follow its status. {STATUS_ONLY}
      </p>
      <div>
        <a
          href={GITHUB_START_PATH}
          className="inline-flex items-center rounded-sm bg-brand px-3 py-1.5 font-medium text-brand-contrast text-sm hover:bg-brand-hover"
        >
          Connect GitHub
        </a>
      </div>
    </section>
  );
}

function Setup({ connection }: { connection: ConnectionView }) {
  const row = setupRow(connection);
  const test = useConnectionTest();
  const stop = useDisconnect();

  const startAgain = () =>
    stop.mutate(connection.id, { onSuccess: () => window.location.assign(GITHUB_START_PATH) });

  return (
    <section aria-label="GitHub setup" className="flex flex-col gap-3">
      <h2 className="font-medium font-serif text-base">Setting up GitHub</h2>
      <p className="max-w-prose text-ink text-sm" role="status">
        {row.words}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        {row.action === "start_again" ? (
          <Button pending={stop.isPending} pendingLabel="Starting again…" onClick={startAgain}>
            {ACTION_LABEL.start_again}
          </Button>
        ) : row.action === "install" || row.action === "choose_repositories" ? (
          <a
            href={connection.install_url ?? "https://github.com/settings/installations"}
            className="inline-flex items-center rounded-sm bg-brand px-3 py-1.5 font-medium text-brand-contrast text-sm hover:bg-brand-hover"
          >
            {ACTION_LABEL[row.action]}
          </a>
        ) : row.action === "test_again" ? (
          <Button
            pending={test.isPending}
            pendingLabel="Testing…"
            onClick={() => test.mutate(connection.id)}
          >
            {ACTION_LABEL.test_again}
          </Button>
        ) : null}
        <Button
          variant="secondary"
          pending={stop.isPending && row.action !== "start_again"}
          pendingLabel="Stopping…"
          onClick={() => stop.mutate(connection.id)}
        >
          Stop setting up
        </Button>
      </div>
      <TestOutcome result={test.data} error={test.error ?? stop.error} />
    </section>
  );
}

function Connected({ connection }: { connection: ConnectionView }) {
  const test = useConnectionTest();
  const [replacing, setReplacing] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

  return (
    <section aria-label="GitHub" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="font-medium font-serif text-base">
          GitHub App <span className="font-mono text-sm">{connection.app_slug}</span>
        </h2>
        <p className="max-w-prose text-ink text-sm" role="status">
          {standing(connection)}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          pending={test.isPending}
          pendingLabel="Testing…"
          onClick={() => test.mutate(connection.id)}
        >
          Test this connection
        </Button>
        <Button variant="secondary" onClick={() => setReplacing((open) => !open)}>
          Replace the App key
        </Button>
        <Button variant="secondary" onClick={() => setDisconnecting(true)}>
          Disconnect
        </Button>
      </div>
      <TestOutcome result={test.data} error={test.error} />
      {replacing ? <ReplaceKey connectionId={connection.id} /> : null}
      {disconnecting ? (
        <Disconnect connectionId={connection.id} onCancel={() => setDisconnecting(false)} />
      ) : null}
      <Repositories places={connection.repositories} />
    </section>
  );
}

function standing(connection: ConnectionView): string {
  if (connection.state === "failing" && connection.failing_since) {
    return `Failing since ${formatTimestamp(connection.failing_since)}. nexovue keeps trying.`;
  }
  if (connection.state === "needs_attention") {
    return `Needs attention. ${failureFix(connection.last_failure)}`;
  }
  return connection.last_ok_at
    ? `Working. Last synced ${formatRelativeTime(connection.last_ok_at)}.`
    : "Working. Not synced yet.";
}

function TestOutcome({
  result,
  error,
}: {
  result: ConnectionTestResult | undefined;
  error: unknown;
}) {
  if (error) return <InlineFailure title="That did not go through" error={error} />;
  if (!result) return null;
  const words = result.pending
    ? "The test has not finished. Its result will show here when it does."
    : result.ok
      ? TEST_WORKS
      : failureFix(result.failure);
  return (
    <p role="status" className="max-w-prose text-ink text-sm">
      {words}
    </p>
  );
}

function ReplaceKey({ connectionId }: { connectionId: string }) {
  const replace = useReplaceAppKey();

  const read = async (file: File | undefined) => {
    if (!file) return;
    replace.mutate({ connectionId, privateKey: await file.text() });
  };

  return (
    <div className="flex flex-col gap-2 border-line border-l-2 pl-3">
      <label className="flex max-w-prose flex-col gap-1 text-ink text-sm">
        The App's new private key, the .pem file GitHub gave you. It is sealed in place, tested, and
        every link resumes.
        <input
          type="file"
          accept=".pem"
          onChange={(event) => void read(event.target.files?.[0])}
          className="text-sm"
        />
      </label>
      {replace.isPending ? (
        <p className="text-ink-dim text-sm" role="status">
          Saving and testing…
        </p>
      ) : null}
      <TestOutcome result={replace.data} error={replace.error} />
    </div>
  );
}

function Disconnect({ connectionId, onCancel }: { connectionId: string; onCancel: () => void }) {
  const disconnect = useDisconnect();
  return (
    <ConfirmPanel label="Disconnect GitHub">
      <p className="max-w-prose text-ink text-sm">{DISCONNECT_LEAVES}</p>
      <div className="mt-3 flex flex-wrap gap-3">
        <Button
          pending={disconnect.isPending}
          pendingLabel="Disconnecting…"
          onClick={() => disconnect.mutate(connectionId)}
        >
          Disconnect GitHub
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Keep it
        </Button>
      </div>
      {disconnect.error ? (
        <InlineFailure title="GitHub is still connected" error={disconnect.error} />
      ) : null}
    </ConfirmPanel>
  );
}

function Repositories({ places }: { places: ConnectionPlace[] }) {
  return (
    <section aria-label="Repositories" className="flex flex-col gap-2">
      <h3 className="font-medium text-ink text-sm">Repositories it reaches</h3>
      {places.length === 0 ? (
        <p className="text-ink-dim text-sm">None yet.</p>
      ) : (
        <ul className="border-line border-t">
          {places.map((place) => (
            <li
              key={place.id}
              className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-line-subtle border-b py-2"
            >
              <span className="min-w-0 break-all font-mono text-ink text-sm">{place.key}</span>
              <span className="text-ink-dim text-sm">
                {place.is_private ? "Private" : "Public"}
                {place.is_reachable ? null : " · No longer reached"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
