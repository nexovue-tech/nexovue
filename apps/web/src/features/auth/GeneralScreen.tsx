import { InlineFailure } from "@/shared/components/InlineFailure";
import { SettingGroup, SettingRow } from "@/shared/components/SettingRow";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import { NameSection } from "./account-sections";
import { useSession } from "./api";

/**
 * What you are called and how the console looks to you.
 *
 * **Split from the security half deliberately.** "Profile & security" was two
 * things wearing one name, and they are reached for at different moments: nobody
 * opening the console to change its theme is also auditing what is signed in as
 * them, and somebody who suspects they have been is not there to set a nickname.
 *
 * The theme moved here from the top bar. It sat beside the project and the time
 * window — the two controls that qualify every reading on every screen — where it
 * was the only thing that changed nothing about the data, and it took space on
 * every screen for a decision most people make once.
 */
export function GeneralScreen() {
  const session = useSession();

  if (session.isPending) {
    return (
      <SettingsLayout title="General">
        <SkeletonRows rows={4} label="Loading your account" />
      </SettingsLayout>
    );
  }

  if (session.isError || session.data === undefined) {
    return (
      <SettingsLayout title="General">
        <InlineFailure
          title="Your account could not be loaded"
          error={session.error}
          onRetry={() => void session.refetch()}
        />
      </SettingsLayout>
    );
  }

  const { user, organization } = session.data;

  return (
    <SettingsLayout
      title="General"
      description={
        <>
          Signed in as <span className="text-ink">{user.email}</span> in{" "}
          <span className="text-ink">{organization.name}</span>.
        </>
      }
    >
      <NameSection currentName={user.name} />

      {/* No theme row here. It is one control, in the sidebar footer, where it is
          reachable from every screen — a second one on this page would be a second
          place to change the same thing and a second place to look for it. */}
      <SettingGroup title="Account">
        <SettingRow
          label="Email address"
          description="This is where sign-in links and alerts to you are sent. Write in from it to change it — a new address has to be re-verified before it can receive either."
          control={<span className="text-ink-dim text-sm">{user.email}</span>}
        />
      </SettingGroup>
    </SettingsLayout>
  );
}
