import type { RuleCount, RuleCounts, RuleName } from "./api";

/**
 * `/settings/explanations` in words, pure (F3 FR54; design, *Explanation settings*).
 * Rule names are nexovue's own; the summary a customer may copy holds counts only.
 */

/** What each rule looks for, in the console's words. */
export const RULE_WORDS: Record<RuleName, string> = {
  signed_deploy: "A signed deploy of the service, rare at that hour",
  dependency_finding: "A service it calls opened its own finding first",
  signed_deploy_weak: "A signed deploy of the service, usual at that hour or with little history",
  dependency_deploy: "A signed deploy of a service it calls",
  dependency_edge: "Calls to a service it calls moved in the same minutes",
  traffic_rose: "Traffic rose before a slowdown",
  project_deploy: "A deploy to half or more of a project's services",
  project_shared_dependency: "A service several of them call has its own finding",
};

export const STOP_WORDS = {
  stop: "Stop naming causes from this rule",
  resume: "Let it name causes again",
  confirm:
    "Its facts stay on every explanation; it names no likely cause in this organization from now on. The change is recorded in the audit log.",
  stopped: "Stopped: names no cause here",
} as const;

/** Whether anything has been counted at all, so an empty screen says so plainly. */
export function nothingYet(counts: RuleCounts): boolean {
  return (
    counts.rules.every((rule) => rule.named === 0 && ratingsOf(rule) === 0) &&
    counts.without_cause_useful + counts.without_cause_not_useful === 0
  );
}

function ratingsOf(rule: RuleCount): number {
  return rule.useful + rule.wrong_cause + rule.not_useful + rule.bad_wording;
}

/** One rule's numbers as a sentence. */
export function ruleLine(rule: RuleCount): string {
  const named = `Named a likely cause on ${rule.named} finding${rule.named === 1 ? "" : "s"}`;
  if (ratingsOf(rule) === 0) return `${named}; not rated yet.`;
  return (
    `${named}. Rated useful ${rule.useful}; the cause was wrong ${rule.wrong_cause}; ` +
    `it did not help ${rule.not_useful}; poor AI wording ${rule.bad_wording}.`
  );
}

/**
 * The summary a customer may choose to send to nexovue (FR54, AC99): rule names and
 * counts, nothing typed by anyone and no name from their system.
 */
export function copyableSummary(counts: RuleCounts): string {
  const lines = counts.rules.map(
    (rule) =>
      `${rule.rule}: named=${rule.named} useful=${rule.useful} wrong_cause=${rule.wrong_cause} ` +
      `not_useful=${rule.not_useful} bad_wording=${rule.bad_wording}${rule.stopped ? " stopped" : ""}`,
  );
  lines.push(
    `no_cause: useful=${counts.without_cause_useful} not_useful=${counts.without_cause_not_useful}`,
  );
  return lines.join("\n");
}
