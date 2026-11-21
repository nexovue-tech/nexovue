import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";

import type { IssuePlace } from "./api";
import { useCreateIssue, useIssueConnections, useIssueMappings, useIssuePreview } from "./api";
import { preselectedPlace } from "./link-words";

const PROSE = "max-w-prose";
/** Nothing is connected yet: an admin is pointed at settings, others at an admin. */
export const ASK_AN_ADMIN_TO_CONNECT = "Ask an admin to connect GitHub.";
const NOT_ON_HOSTED = "Tracker connections are not available on the hosted service yet.";
const TURNED_OFF = "Tracker connections are turned off on this install.";
const NOT_READY =
  "The GitHub connection needs attention, so no issue can be created now. The case still works.";

/**
 * Create an issue (FR35, design *Creating an issue*): pick a repository, preselected from
 * the service's mapping when it has one (T6.3), read exactly what will be written, then
 * create. Nothing leaves before the click; the job writes it, and the links list shows
 * when it has (AC83's preview is the issue's bytes).
 */
export function CreateIssuePanel({
  findingId,
  projectId,
  serviceName,
  admin,
  onCreated,
  onCancel,
}: {
  findingId: string;
  projectId: string;
  serviceName: string | null;
  admin: boolean;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const connections = useIssueConnections(true);
  const mappings = useIssueMappings(projectId, serviceName != null);
  // Undefined until the person chooses: until then the mapping's repository stands.
  const [chosenId, setChosenId] = useState<string | null | undefined>(undefined);
  const github = connections.data?.connections.find((connection) => connection.kind === "github");
  const reachable: IssuePlace[] = (github?.repositories ?? []).filter(
    (place) => place.is_reachable,
  );
  const placeId =
    chosenId === undefined
      ? preselectedPlace(reachable, mappings.data ?? [], serviceName)
      : chosenId;
  const preview = useIssuePreview(findingId, placeId);
  const create = useCreateIssue(findingId);

  if (connections.isPending) return <SkeletonRows rows={2} label="Loading the connection" />;
  if (connections.isError) {
    return (
      <InlineFailure
        title="The connection could not be read"
        error={connections.error}
        onRetry={() => void connections.refetch()}
      />
    );
  }
  const overview = connections.data;
  const places = reachable;

  let blocked: React.ReactNode = null;
  if (!overview.available) {
    blocked = overview.unavailable_reason === "hosted" ? NOT_ON_HOSTED : TURNED_OFF;
  } else if (github == null || github.state === "setting_up") {
    blocked = admin ? (
      <>
        GitHub is not connected.{" "}
        <Link
          to="/settings/connections"
          className="font-medium text-brand-text underline-offset-2 hover:underline"
        >
          Connect it in settings
        </Link>
      </>
    ) : (
      ASK_AN_ADMIN_TO_CONNECT
    );
  } else if (github.state === "needs_attention") {
    blocked = NOT_READY;
  } else if (places.length === 0) {
    blocked = "The GitHub App reaches no repository nexovue can write to.";
  }

  const chosen = places.find((place) => place.id === placeId) ?? null;
  return (
    <ConfirmPanel label="Create an issue">
      {blocked != null ? (
        <p className={PROSE}>{blocked}</p>
      ) : (
        <>
          <SelectField
            label="Repository"
            stacked
            value={placeId ?? ""}
            options={[{ id: "", key: "Choose a repository" }, ...places]}
            optionLabel={(place) => place.key}
            onChange={(id) => setChosenId(id === "" ? null : id)}
          />
          {placeId != null && preview.isPending ? (
            <SkeletonRows rows={3} label="Reading what will be written" />
          ) : null}
          {preview.isError ? (
            <InlineFailure title="The preview could not be read" error={preview.error} />
          ) : null}
          {preview.data != null ? (
            <div className="flex flex-col gap-2" data-testid="issue-preview">
              <p className={`text-ink-dim text-sm ${PROSE}`}>
                Exactly what will be written to {preview.data.place_key}:
              </p>
              <p className="font-medium text-ink text-sm">{preview.data.title}</p>
              <pre className="whitespace-pre-wrap break-words rounded border border-line p-2 font-mono text-ink text-xs">
                {preview.data.body}
              </pre>
            </div>
          ) : null}
          {preview.data?.refusal != null ? (
            <p className={`text-sm text-warning-text ${PROSE}`}>{preview.data.refusal}</p>
          ) : null}
        </>
      )}
      <div className="flex gap-2">
        {blocked == null &&
        chosen != null &&
        preview.data != null &&
        preview.data.refusal == null ? (
          <Button
            pending={create.isPending}
            pendingLabel="Creating"
            onClick={() => create.mutate(chosen.id, { onSuccess: () => onCreated() })}
          >
            Create issue in {chosen.key}
          </Button>
        ) : null}
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      {create.isError ? (
        <InlineFailure title="The issue was not asked for" error={create.error} />
      ) : null}
    </ConfirmPanel>
  );
}
