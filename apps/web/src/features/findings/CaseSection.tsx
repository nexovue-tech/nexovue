import { Link } from "@tanstack/react-router";
import { useRef, useState } from "react";

import { ApiError, isConflict } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { useCan, useSession } from "@/shared/identity";

import type { EmailOutcome, FindingDetail } from "./api";
import { useCaseChange, useRootCase } from "./api";
import { CaseLine } from "./CaseLine";
import { CreateIssuePanel } from "./CreateIssuePanel";
import type { CaseAction } from "./finding-page";
import {
  caseActions,
  caseDoneSentence,
  caseEndingSentence,
  earlierCaseLine,
  joinedLeadLine,
  keptSentence,
  SIGNAL_NAMES,
} from "./finding-page";
import { LinksList } from "./LinksList";
import type { IssueOutcome } from "./link-words";
import { holdLine, resolveChoice, resolveDoneSentence, syncedLink } from "./link-words";
import { NoteBox } from "./NoteBox";
import { ReassignPanel } from "./ReassignPanel";
import { Timeline } from "./Timeline";

const PROSE = "max-w-prose";
/** FR46: each case action at least 44 px tall and on its own row, never beside
 * another, so Resolve can never sit next to Reassign. */
const CASE_ACTION = "min-h-11 w-full max-w-xs justify-center sm:w-64";

/**
 * The work half of the finding page (F22 design, *The finding page*): the case
 * sentence, the labelled case actions, the note box and the timeline. On a
 * joined finding it leads with the case it is part of and binds every act to
 * the root (FR12); the server routes the writes there regardless.
 */
export function CaseSection({
  finding,
  zone,
  onReload,
  onOwnAct,
}: {
  finding: FindingDetail;
  zone?: string;
  onReload: () => void;
  onOwnAct: () => void;
}) {
  const session = useSession();
  const inSupportView = Boolean(session.data?.support_view);
  const canOperate = useCan("operate_cases") && !inSupportView;
  const admin = useCan("manage_connections");
  const viewerId = session.data?.user.id ?? null;
  const rootQuery = useRootCase(finding.root_finding_id);
  const change = useCaseChange(finding.id);
  // FR13: Reopen that case acts on the earlier case, not on this finding.
  const reopenEarlier = useCaseChange(finding.earlier_case?.id ?? finding.id);
  const [armed, setArmed] = useState<"resolve" | "reassign" | "create-issue" | null>(null);
  const [done, setDone] = useState("");
  const [changedMeanwhile, setChangedMeanwhile] = useState("");
  const line = useRef<HTMLParagraphElement>(null);

  const isJoined = finding.root_finding_id != null;
  const root = isJoined ? rootQuery.data : finding;
  if (isJoined && rootQuery.isPending) {
    return <SkeletonRows rows={3} label="Loading the case" />;
  }
  if (root == null) {
    return (
      <InlineFailure
        title="The case could not be loaded"
        error={rootQuery.error}
        onRetry={() => void rootQuery.refetch()}
      />
    );
  }

  const actions = caseActions(root, { canOperateCases: canOperate, viewerId });
  const hasPagerDuty = Boolean(root.project_has_pagerduty);

  const landOnTheLine = () => setTimeout(() => line.current?.focus(), 0);

  const choice = resolveChoice(root);
  const issueOutcome = (input: Parameters<typeof change.mutate>[0]): IssueOutcome | null => {
    if (choice.kind === "held") return "already_closed";
    if (choice.kind !== "synced") return null;
    return input.leave_issue_open ? "left_open" : "will_close";
  };
  const doneSentence = (action: CaseAction, input: Parameters<typeof change.mutate>[0]) => {
    const outcome = action === "resolve" ? issueOutcome(input) : null;
    if (outcome == null || (choice.kind !== "synced" && choice.kind !== "held")) {
      return caseDoneSentence(action, { hasPagerDuty });
    }
    // Spec, *Words after an action*: the issue's line, then FR48's when it applies.
    const pagerDuty = hasPagerDuty
      ? " PagerDuty is not told, so a live page stays until the signal recovers."
      : "";
    return `${resolveDoneSentence(choice.issueKey, outcome)}${pagerDuty}`;
  };

  const run = (action: CaseAction, input: Parameters<typeof change.mutate>[0]) => {
    setDone("");
    setChangedMeanwhile("");
    onOwnAct();
    change.mutate(input, {
      onSuccess: () => {
        setArmed(null);
        setDone(doneSentence(action, input));
        landOnTheLine();
      },
      onError: (error) => {
        if (!isConflict(error)) return;
        setArmed(null);
        setChangedMeanwhile(
          error instanceof ApiError && error.problem?.detail
            ? `${error.problem.detail} It is shown as it is now.`
            : "This case changed since you opened it. It is shown as it is now.",
        );
        onReload();
      },
    });
  };

  const leadingSignal = finding.signals[0]?.signal;
  const earlier = isJoined ? null : (finding.earlier_case ?? null);
  const ending = caseEndingSentence(root, zone);
  const kept = keptSentence(root, zone);
  const hold = root.case_state === "investigating" ? holdLine(root, zone) : null;
  const links = root.links ?? [];
  const canCreateIssue = canOperate && root.case_state !== "resolved" && syncedLink(root) == null;

  return (
    <section aria-label="The case" className="flex flex-col gap-3">
      <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">The case</h2>
      {isJoined ? (
        <p className={`text-ink text-sm ${PROSE}`}>
          <Link
            to="/findings/$findingId"
            params={{ findingId: root.id }}
            className="font-medium text-brand-text underline-offset-2 hover:underline"
          >
            {joinedLeadLine(root, zone)}
          </Link>
          {leadingSignal ? (
            // FR12: name the signal that moved here, so a mismatch is visible.
            <span className="text-ink-dim">
              {" "}
              · {SIGNAL_NAMES[leadingSignal]} moved on this finding
            </span>
          ) : null}
        </p>
      ) : null}
      <CaseLine ref={line} view={{ finding: root, root, viewerId, zone }} />
      {hold != null ? (
        // D73, AC93: the tracker closed it while the problem is still measured.
        <p className={`text-ink text-sm ${PROSE}`} data-testid="hold-line">
          {hold}
        </p>
      ) : null}
      {earlier != null ? (
        // FR9, FR13: the service's earlier case, named and linked.
        <p className={`text-ink text-sm ${PROSE}`}>
          <Link
            to="/findings/$findingId"
            params={{ findingId: earlier.id }}
            className="font-medium text-brand-text underline-offset-2 hover:underline"
          >
            {earlierCaseLine(earlier, zone).sentence}
          </Link>
        </p>
      ) : null}
      {ending != null ? <p className={`text-ink text-sm ${PROSE}`}>{ending}</p> : null}
      {kept != null ? <p className={`text-ink-dim text-sm ${PROSE}`}>{kept}</p> : null}

      {canOperate ? (
        <div className="flex flex-col gap-2">
          {actions.takeThis ? (
            <Button
              className={CASE_ACTION}
              pending={change.isPending}
              pendingLabel="Taking"
              onClick={() => run("take", { case_state: "investigating" })}
            >
              Take this
            </Button>
          ) : null}
          {actions.reopenEarlier != null ? (
            <Button
              variant="secondary"
              className={CASE_ACTION}
              pending={reopenEarlier.isPending}
              pendingLabel="Reopening"
              onClick={() => {
                setDone("");
                onOwnAct();
                reopenEarlier.mutate(
                  { case_state: "investigating" },
                  {
                    onSuccess: () => {
                      setDone(caseDoneSentence("reopen"));
                      onReload();
                      landOnTheLine();
                    },
                  },
                );
              }}
            >
              Reopen that case
            </Button>
          ) : null}
          {actions.takeOverFrom != null ? (
            <Button
              variant="secondary"
              className={CASE_ACTION}
              pending={change.isPending}
              pendingLabel="Taking over"
              onClick={() =>
                run("assign", {
                  assignee_id: viewerId,
                  expected_assignee_id: root.assignee_id ?? null,
                })
              }
            >
              Take over from {actions.takeOverFrom}
            </Button>
          ) : null}
          {actions.resolve ? (
            <Button
              className={CASE_ACTION}
              pending={change.isPending && armed !== "resolve"}
              pendingLabel="Resolving"
              onClick={() => {
                // FR6, AC92: one tap only with no synced issue and the signal closed;
                // otherwise a confirmation that names what Resolve does.
                if (choice.kind === "one_tap") run("resolve", { case_state: "resolved" });
                else setArmed("resolve");
              }}
            >
              Resolve
            </Button>
          ) : null}
          {actions.reassign ? (
            <Button
              variant="secondary"
              className={CASE_ACTION}
              onClick={() => setArmed("reassign")}
            >
              Reassign
            </Button>
          ) : null}
          {canCreateIssue ? (
            <Button
              variant="secondary"
              className={CASE_ACTION}
              onClick={() => setArmed("create-issue")}
            >
              Create an issue
            </Button>
          ) : null}
          {actions.reopen ? (
            <Button
              className={CASE_ACTION}
              pending={change.isPending}
              pendingLabel="Reopening"
              onClick={() => run("reopen", { case_state: "investigating" })}
            >
              Reopen
            </Button>
          ) : null}
        </div>
      ) : null}

      {armed === "resolve" && choice.kind !== "one_tap" ? (
        <ConfirmPanel label="Resolve">
          {choice.sentences.map((sentence) => (
            <p key={sentence} className={PROSE}>
              {sentence}
            </p>
          ))}
          {choice.kind === "signal_open" ? (
            <p className={PROSE}>Resolving says the work is done anyway.</p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button
              pending={change.isPending && !change.variables?.leave_issue_open}
              pendingLabel="Resolving"
              onClick={() => run("resolve", { case_state: "resolved" })}
            >
              {choice.kind === "synced" ? "Resolve and close the issue" : "Resolve"}
            </Button>
            {choice.kind === "synced" ? (
              <Button
                variant="secondary"
                pending={change.isPending && Boolean(change.variables?.leave_issue_open)}
                pendingLabel="Resolving"
                onClick={() => run("resolve", { case_state: "resolved", leave_issue_open: true })}
              >
                Resolve here, leave the issue open
              </Button>
            ) : null}
            <Button
              variant="secondary"
              onClick={() => {
                setArmed(null);
                landOnTheLine();
              }}
            >
              Cancel
            </Button>
          </div>
        </ConfirmPanel>
      ) : null}
      {armed === "reassign" ? (
        <ReassignPanel
          finding={finding}
          root={root}
          onDone={(name, outcome: EmailOutcome) => {
            setArmed(null);
            onOwnAct();
            setDone(
              name == null
                ? caseDoneSentence("unassign")
                : caseDoneSentence("assign", { assigneeName: name, emailOutcome: outcome }),
            );
            landOnTheLine();
          }}
          onConflict={(detail) => {
            setArmed(null);
            setChangedMeanwhile(`${detail} It is shown as it is now.`);
            onReload();
          }}
          onCancel={() => {
            setArmed(null);
            landOnTheLine();
          }}
        />
      ) : null}

      <p role="status" className={`text-ink text-sm ${PROSE}`}>
        {done}
      </p>
      {changedMeanwhile ? (
        <p role="alert" className={`text-sm text-warning-text ${PROSE}`}>
          {changedMeanwhile}
        </p>
      ) : null}
      {change.isError && !isConflict(change.error) ? (
        <InlineFailure title="That did not go through" error={change.error} />
      ) : null}
      {reopenEarlier.isError ? (
        <InlineFailure title="That case could not be reopened" error={reopenEarlier.error} />
      ) : null}

      {armed === "create-issue" ? (
        <CreateIssuePanel
          findingId={finding.id}
          admin={admin}
          onCreated={() => {
            setArmed(null);
            onOwnAct();
            setDone("Asked for the issue. The link below shows when it exists.");
            landOnTheLine();
          }}
          onCancel={() => {
            setArmed(null);
            landOnTheLine();
          }}
        />
      ) : null}

      {inSupportView ? (
        <p className={`text-ink-dim text-sm ${PROSE}`}>Hidden during support view</p>
      ) : (
        <NoteBox findingId={finding.id} />
      )}

      <LinksList
        findingId={finding.id}
        links={links}
        caseActive={root.case_state === "investigating"}
        admin={admin}
        canOperate={canOperate}
        viewerId={viewerId}
        zone={zone}
      />

      <h3 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Timeline</h3>
      <Timeline findingId={finding.id} zone={zone} />
    </section>
  );
}
