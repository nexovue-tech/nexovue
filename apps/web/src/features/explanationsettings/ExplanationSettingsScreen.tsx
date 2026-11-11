import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { CopyButton } from "@/shared/components/CopyButton";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { useCan } from "@/shared/identity";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import { type RuleCount, useRuleCounts, useSetRuleStopped } from "./api";
import {
  copyableSummary,
  nothingYet,
  RULE_WORDS,
  ruleLine,
  STOP_WORDS,
} from "./explanation-settings";

/**
 * `/settings/explanations` (F3 FR54, AC99): how each rule's likely causes were rated,
 * and stopping a rule naming causes in this organization. Not an AI screen: it stands
 * with AI off. For those who may configure detection; others read nothing here.
 */
export function ExplanationSettingsScreen() {
  const may = useCan("configure_detection");
  const counts = useRuleCounts(may);
  return (
    <SettingsLayout
      title="Explanations"
      description="How each rule's likely causes were rated on findings, and the rules this organization has stopped naming causes."
    >
      {!may ? (
        <p className="max-w-prose text-ink-dim text-sm">
          An owner or an admin can see and change how explanations name causes.
        </p>
      ) : counts.isPending ? (
        <SkeletonRows rows={4} label="Loading the rule counts" />
      ) : counts.isError ? (
        <InlineFailure
          title="The rule counts could not be loaded"
          error={counts.error}
          onRetry={() => void counts.refetch()}
        />
      ) : (
        <div className="flex flex-col gap-4">
          {nothingYet(counts.data) ? (
            <p className="text-ink-dim text-sm">No findings yet.</p>
          ) : null}
          <ul className="flex flex-col gap-4">
            {counts.data.rules.map((rule) => (
              <Rule key={rule.rule} rule={rule} />
            ))}
          </ul>
          <section aria-labelledby="explanations-summary" className="flex flex-col gap-2">
            <h2 id="explanations-summary" className="font-medium font-serif text-base text-ink">
              A summary you can send
            </h2>
            <p className="max-w-prose text-ink-dim text-sm">
              Counts only: no names from your system and nothing anyone typed. Send it to nexovue if
              you choose to; nothing is sent for you.
            </p>
            <pre
              data-testid="explanations-summary"
              className="whitespace-pre-wrap break-words rounded-sm border border-line p-3 font-mono text-ink text-xs"
            >
              {copyableSummary(counts.data)}
            </pre>
            <CopyButton value={copyableSummary(counts.data)} label="Copy the summary" />
          </section>
        </div>
      )}
    </SettingsLayout>
  );
}

function Rule({ rule }: { rule: RuleCount }) {
  const change = useSetRuleStopped();
  const [confirming, setConfirming] = useState(false);
  return (
    <li className="flex flex-col gap-1">
      <h2 className="font-medium text-ink text-sm">{RULE_WORDS[rule.rule]}</h2>
      <p className="text-ink-dim text-sm">{ruleLine(rule)}</p>
      {rule.stopped ? <p className="text-ink text-sm">{STOP_WORDS.stopped}</p> : null}
      {confirming ? (
        <ConfirmPanel label={STOP_WORDS.stop}>
          <p className="max-w-prose">{STOP_WORDS.confirm}</p>
          <div className="flex flex-wrap gap-2">
            <Button
              pending={change.isPending}
              onClick={() =>
                change.mutate(
                  { rule: rule.rule, stopped: true },
                  { onSuccess: () => setConfirming(false) },
                )
              }
            >
              {STOP_WORDS.stop}
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </ConfirmPanel>
      ) : (
        <div>
          <Button
            variant="secondary"
            pending={change.isPending}
            onClick={() =>
              rule.stopped
                ? change.mutate({ rule: rule.rule, stopped: false })
                : setConfirming(true)
            }
          >
            {rule.stopped ? STOP_WORDS.resume : STOP_WORDS.stop}
          </Button>
        </div>
      )}
      {change.error ? (
        <InlineFailure title="The rule was not changed" error={change.error} />
      ) : null}
    </li>
  );
}
