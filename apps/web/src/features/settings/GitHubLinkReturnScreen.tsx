import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { ApiError, apiClient } from "@/shared/api-client";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { LINK_FALLBACK_PATH, takeLinkReturn } from "@/shared/github-link-return";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import { LINK_WORDS, linkOutcome } from "./link-words";

interface Attempt {
  id: string;
  status: "waiting" | "running" | "linked" | "refused" | "failed";
  reason: string | null;
}

/** The attempt is polled this often, at most this many times, then said not to have finished. */
const POLL_MS = 2000;
const POLLS = 30;

/**
 * F5 (design, *GitHub account link*; status decision 34): where GitHub returns a person
 * linking their own account, with `code` and `state`. Posted to the API, never acted on in
 * a `GET`, so the support view's read-only rule holds; then the attempt is polled until
 * GitHub's answer is in, and the person goes back to the finding they came from.
 */
export function GitHubLinkReturnScreen() {
  const search = useRouterState({ select: (state) => state.location.searchStr });
  const navigate = useNavigate();
  const [words, setWords] = useState<string | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [back, setBack] = useState(LINK_FALLBACK_PATH);
  // Once per page: the `state` is spent by the first post.
  const sent = useRef(false);

  useEffect(() => {
    if (sent.current) return;
    sent.current = true;
    const remembered = takeLinkReturn();
    const destination = remembered?.path ?? LINK_FALLBACK_PATH;
    setBack(destination);
    const parameters = new URLSearchParams(search);
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const settle = (attempt: Attempt, polls: number) => {
      if (stopped) return;
      const outcome = linkOutcome(attempt.status, attempt.reason, polls >= POLLS);
      if (outcome === "linked") {
        void navigate({ to: destination });
      } else if (outcome !== null) {
        setWords(outcome);
      } else {
        timer = setTimeout(() => {
          apiClient
            .get<Attempt>(`connections/github/user-link/attempts/${encodeURIComponent(attempt.id)}`)
            .then((next) => settle(next, polls + 1), setFailure);
        }, POLL_MS);
      }
    };
    apiClient
      .post<Attempt>("connections/github/user-link/complete", {
        code: parameters.get("code") ?? "",
        state: parameters.get("state") ?? "",
      })
      .then(
        (attempt) => settle(attempt, 0),
        (error: unknown) => {
          if (error instanceof ApiError && error.problem?.detail) setWords(error.problem.detail);
          else setFailure(error);
        },
      );
    return () => {
      stopped = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [search, navigate]);

  return (
    <SettingsLayout title={LINK_WORDS.title} description={LINK_WORDS.description}>
      {words ? (
        <div className="flex max-w-prose flex-col gap-3">
          <p className="text-ink text-sm" role="status">
            {words}
          </p>
          <a href={back} className="text-brand-text text-sm underline">
            {LINK_WORDS.back}
          </a>
        </div>
      ) : failure ? (
        <InlineFailure title={LINK_WORDS.failed} error={failure} />
      ) : (
        <SkeletonRows rows={2} label={LINK_WORDS.waiting} />
      )}
    </SettingsLayout>
  );
}
