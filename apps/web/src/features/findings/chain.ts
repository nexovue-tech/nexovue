import type { CaseState, SourceState } from "./api";

/**
 * The strip under a finding's title (UX review 2026-11-28, approved drawing C): the five steps
 * from a finding to a pull request, each with its state in a few words, and the one step that
 * is next. Pure, so every state is tested without a page.
 */

export type StepStatus = "done" | "now" | "later" | "skipped";

export interface ChainStep {
  name: string;
  state: string;
  detail: string | null;
  status: StepStatus;
  /** Where on the page the step's own section starts. */
  anchor: string;
}

export interface ChainInput {
  findingState: string;
  severity: string;
  caseState: CaseState | null;
  assigneeName: string | null;
  /** The case's synced issue: `acme/shop#1`, or null while it is being created. */
  issue: { key: string | null; state: "open" | "closed" | null } | null;
  /** The newest suggested fix's state: from the service's own files, else the general one. */
  fixState: SourceState | null;
  /** Whether a suggested fix can be asked for here at all. */
  fixOffered: boolean;
  pullRequestState: string | null;
}

export const CHAIN_ANCHORS = {
  finding: "finding-evidence",
  case: "the-case",
  fix: "suggested-fix",
} as const;

const FIX_WORDS: Record<SourceState, string> = {
  gathering: "Being written",
  gathered: "Files ready to confirm",
  proposing: "Being written",
  proposed: "Ready to read",
  approved: "Read",
  opened: "Draft opened",
  refused: "Refused by the checks",
  failed: "Could not finish",
  stale: "Out of date",
};

const FIX_DONE: ReadonlySet<SourceState> = new Set(["proposed", "approved", "opened"]);
const FIX_UNDER_WAY: ReadonlySet<SourceState> = new Set(["gathering", "gathered", "proposing"]);

const PULL_REQUEST_WORDS: Record<string, string> = {
  open: "Draft open",
  merged: "Merged",
  closed: "Closed without merging",
};

interface Draft {
  name: string;
  state: string;
  detail: string | null;
  anchor: string;
  done: boolean;
  /** Started and waiting on someone: it is the next step whatever came before. */
  underWay: boolean;
}

export function chainSteps(input: ChainInput): ChainStep[] {
  const drafts: Draft[] = [
    findingStep(input),
    caseStep(input),
    issueStep(input),
    fixStep(input),
    pullRequestStep(input),
  ];
  const finished = input.caseState === "resolved";
  const underWay = drafts.findIndex((step) => step.underWay);
  const furthestDone = drafts.reduce((last, step, index) => (step.done ? index : last), -1);
  const next = finished
    ? -1
    : underWay !== -1
      ? underWay
      : furthestDone + 1 < drafts.length
        ? furthestDone + 1
        : -1;
  const reached = Math.max(furthestDone, next);
  return drafts.map(({ name, state, detail, anchor, done }, index) => ({
    name,
    state,
    detail,
    anchor,
    status: done
      ? "done"
      : index === next
        ? "now"
        : index < reached || finished
          ? "skipped"
          : "later",
  }));
}

function findingStep(input: ChainInput): Draft {
  return {
    name: "Finding",
    state: input.findingState === "open" ? "Open" : "Closed",
    detail: input.severity === "critical" ? "Critical" : "Warning",
    anchor: CHAIN_ANCHORS.finding,
    done: true,
    underWay: false,
  };
}

function caseStep(input: ChainInput): Draft {
  const base = { name: "Case", anchor: CHAIN_ANCHORS.case, underWay: false };
  if (input.caseState === null) {
    return { ...base, state: "Not started", detail: "Take it to start", done: false };
  }
  if (input.caseState === "resolved") {
    return { ...base, state: "Resolved", detail: null, done: true };
  }
  return {
    ...base,
    state: "Being investigated",
    detail: input.assigneeName ? `${input.assigneeName} is on it` : "Nobody assigned",
    done: true,
  };
}

function issueStep(input: ChainInput): Draft {
  const base = { name: "GitHub issue", anchor: CHAIN_ANCHORS.case, underWay: false };
  if (input.issue === null) return { ...base, state: "None", detail: null, done: false };
  if (input.issue.key === null) {
    return { ...base, state: "Being created", detail: null, done: false, underWay: true };
  }
  const [place, number] = input.issue.key.split("#");
  const state = input.issue.state ?? "";
  return {
    ...base,
    state: `#${number ?? input.issue.key} ${state}`.trim(),
    detail: number === undefined ? null : (place ?? null),
    done: true,
  };
}

function fixStep(input: ChainInput): Draft {
  const base = { name: "Suggested fix", anchor: CHAIN_ANCHORS.fix };
  if (input.fixState === null) {
    return {
      ...base,
      state: input.fixOffered ? "Not asked" : "Not offered",
      detail: null,
      done: false,
      underWay: false,
    };
  }
  return {
    ...base,
    state: FIX_WORDS[input.fixState],
    detail: null,
    done: FIX_DONE.has(input.fixState),
    underWay: FIX_UNDER_WAY.has(input.fixState),
  };
}

function pullRequestStep(input: ChainInput): Draft {
  const base = { name: "Pull request", anchor: CHAIN_ANCHORS.fix, detail: null };
  const state = input.pullRequestState;
  if (state === null) return { ...base, state: "Not opened", done: false, underWay: false };
  return {
    ...base,
    state: PULL_REQUEST_WORDS[state] ?? state,
    done: state !== "open",
    underWay: state === "open",
  };
}

/** A feed row's chain past its case, as the API reads it (`FeedChain`). */
export interface FeedChain {
  issue_key: string | null;
  issue_state: "open" | "closed" | null;
  fix_state: string | null;
  pull_request_state: string | null;
}

export interface FeedChip {
  label: string;
  /** `next`: waiting on a person, so it carries the brand colour. */
  tone: "plain" | "next";
}

const FIX_CHIPS: Record<string, FeedChip> = {
  gathering: { label: "Fix being written", tone: "plain" },
  proposing: { label: "Fix being written", tone: "plain" },
  gathered: { label: "Files to confirm", tone: "next" },
  proposed: { label: "Fix ready", tone: "next" },
  approved: { label: "Fix read", tone: "plain" },
};

const PULL_REQUEST_CHIPS: Record<string, FeedChip> = {
  open: { label: "Draft PR open", tone: "next" },
  merged: { label: "PR merged", tone: "plain" },
  closed: { label: "PR closed", tone: "plain" },
};

/**
 * The chips on a feed row (drawing C): the case, its issue, and the furthest of the fix and
 * its pull request. A refused, failed or stale fix shows no chip: the row says nothing it
 * would have to take back.
 */
export function feedChips({
  caseState,
  chain,
}: {
  caseState: CaseState | null;
  chain: FeedChain | null | undefined;
}): FeedChip[] {
  const chips: FeedChip[] = [];
  if (caseState !== null) {
    chips.push({ label: caseState === "resolved" ? "Resolved" : "Case", tone: "plain" });
  }
  if (!chain) return chips;
  if (chain.issue_key) {
    const number = chain.issue_key.split("#")[1] ?? chain.issue_key;
    const closed = chain.issue_state === "closed" ? " closed" : "";
    chips.push({ label: `Issue #${number}${closed}`, tone: "plain" });
  }
  const pullRequest = chain.pull_request_state
    ? PULL_REQUEST_CHIPS[chain.pull_request_state]
    : undefined;
  const fix = chain.fix_state ? FIX_CHIPS[chain.fix_state] : undefined;
  const furthest = pullRequest ?? fix;
  if (furthest) chips.push(furthest);
  return chips;
}
