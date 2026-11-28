/**
 * Findings' Get started (UX review 2026-11-28, approved sketch H): a new organization's first
 * steps, each read from what is already set, so nothing is asked twice. Pure, so every state is
 * tested without a page; `GetStarted.tsx` reads the API into `GetStartedInput`.
 */

export type GetStartedPlace = "services" | "alerts" | "verify" | "deploys" | "github" | "ai";

export interface GetStartedStep {
  title: string;
  detail: string;
  done: boolean;
  /** The first step not done: the box's one filled button. */
  next: boolean;
  place: GetStartedPlace;
}

export interface GetStartedInput {
  /** Undefined while unread; a step it cannot read is never counted as done. */
  servicesReporting: number | undefined;
  alertsReach: boolean | undefined;
  emailVerified: boolean;
  deploysReported: boolean | undefined;
  githubConnected: boolean | undefined;
  aiOn: boolean | undefined;
  /** Which steps this person can do; the others are not shown to them. */
  can: { deploys: boolean; github: boolean; ai: boolean };
}

export interface GetStartedView {
  steps: GetStartedStep[];
  done: number;
  /** Every step shown is done: the box is not shown at all. */
  finished: boolean;
}

export function getStartedSteps(input: GetStartedInput): GetStartedView {
  const all: Array<Omit<GetStartedStep, "next"> & { shown: boolean }> = [
    {
      title: "Send telemetry",
      detail: "A service reports to this project",
      done: (input.servicesReporting ?? 0) > 0,
      place: "services",
      shown: true,
    },
    {
      title: "Alerts reach you",
      detail: "A rule sends what is wrong somewhere you will see it",
      done: input.alertsReach === true,
      place: "alerts",
      shown: true,
    },
    {
      title: "Verify your email",
      detail: "Alerts go only to a verified address",
      done: input.emailVerified,
      place: "verify",
      shown: true,
    },
    {
      title: "Report deploys",
      detail: "So a finding can name the deploy before it",
      done: input.deploysReported === true,
      place: "deploys",
      shown: input.can.deploys,
    },
    {
      title: "Connect GitHub",
      detail: "Turn a finding into an issue, and later a pull request",
      done: input.githubConnected === true,
      place: "github",
      shown: input.can.github,
    },
    {
      title: "Set up AI (optional)",
      detail: "Plainer words, chat and suggested fixes, with your own AI account",
      done: input.aiOn === true,
      place: "ai",
      shown: input.can.ai,
    },
  ];
  const shown = all.filter((step) => step.shown);
  const firstOpen = shown.findIndex((step) => !step.done);
  const steps = shown.map(({ shown: _shown, ...step }, index) => ({
    ...step,
    next: index === firstOpen,
  }));
  const done = steps.filter((step) => step.done).length;
  return { steps, done, finished: done === steps.length };
}

const LINKS: Record<Exclude<GetStartedPlace, "verify">, { to: string; words: string }> = {
  services: { to: "/services", words: "Services" },
  alerts: { to: "/alerts", words: "Alerts" },
  deploys: { to: "/settings/projects", words: "Set up deploys" },
  github: { to: "/settings/connections", words: "GitHub" },
  ai: { to: "/settings/ai", words: "AI" },
};

/** Where a step's link goes. With nothing reported, the empty Services list would teach
 * nothing, so it leads to the setup steps instead. */
export function stepLink(step: { place: Exclude<GetStartedPlace, "verify">; done: boolean }): {
  to: string;
  words: string;
} {
  if (step.place === "services" && !step.done) return { to: "/onboarding", words: "Show me how" };
  return LINKS[step.place];
}
