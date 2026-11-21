import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/shared/api-client";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import { completeGitHub, refreshGitHub } from "./api";
import { EXPIRED_SETUP, INVALID_SETUP } from "./connection-words";

const SETTINGS_PATH = "/settings/connections";

/**
 * Where GitHub's manifest flow returns a person, with `code` and `state` (design, step 3).
 * Read from the raw address, so a code that looks like a number stays text, and posted to
 * the API; writing on a POST keeps this under the support view's read-only rule.
 */
export function GitHubReturnScreen() {
  const search = useRouterState({ select: (state) => state.location.searchStr });
  const navigate = useNavigate();
  const [refusal, setRefusal] = useState<string | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  // Once per page: the `state` is spent by the first post, so a second (React's development
  // double-invoke, a re-mount) would read as "not valid" for a setup that worked.
  const sent = useRef(false);

  useEffect(() => {
    if (sent.current) return;
    sent.current = true;
    const parameters = new URLSearchParams(search);
    completeGitHub(parameters.get("code") ?? "", parameters.get("state") ?? "").then(
      () => void navigate({ to: SETTINGS_PATH }),
      (error: unknown) => {
        const reason = error instanceof ApiError ? error.problem?.reason : undefined;
        if (reason === "expired") setRefusal(EXPIRED_SETUP);
        else if (reason === "invalid") setRefusal(INVALID_SETUP);
        else setFailure(error);
      },
    );
  }, [search, navigate]);

  return (
    <SettingsLayout title="Connect GitHub" description="Finishing the setup GitHub started.">
      {refusal ? (
        <div className="flex max-w-prose flex-col gap-3">
          <p className="text-ink text-sm" role="status">
            {refusal}
          </p>
          <a href={SETTINGS_PATH} className="text-brand-text text-sm underline">
            Start again
          </a>
        </div>
      ) : failure ? (
        <InlineFailure title="Setup did not finish" error={failure} />
      ) : (
        <SkeletonRows rows={2} label="Finishing setup" />
      )}
    </SettingsLayout>
  );
}

/**
 * Where GitHub's install page returns a person. Its `installation_id` is never read (AC38):
 * the API has a job ask GitHub which installation is the App's, then this lands on settings.
 */
export function GitHubInstalledScreen() {
  const navigate = useNavigate();
  const [failure, setFailure] = useState<unknown>(null);
  const sent = useRef(false);

  useEffect(() => {
    if (sent.current) return;
    sent.current = true;
    refreshGitHub().then(() => void navigate({ to: SETTINGS_PATH }), setFailure);
  }, [navigate]);

  return (
    <SettingsLayout
      title="Connect GitHub"
      description="Reading which repositories the App reaches."
    >
      {failure ? (
        <InlineFailure title="The repositories could not be read" error={failure} />
      ) : (
        <SkeletonRows rows={2} label="Reading repositories" />
      )}
    </SettingsLayout>
  );
}
