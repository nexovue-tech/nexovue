import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import {
  ActiveSessionsSection,
  PasswordSection,
  SecondFactorSection,
  SignOutEverywhereSection,
} from "./account-sections";

/**
 * The security half: the password, the second factor in front of it, what is
 * signed in as you, and the control that ends all of it at once.
 *
 * **The order is the order somebody works down it when they are worried.** What
 * is signed in comes before the blunt instrument, because "end the one I do not
 * recognise" is almost always the right answer and "sign out of everything" is
 * the one people reach for only because it was the only one on offer.
 */
export function AccountScreen() {
  return (
    <SettingsLayout
      title="Account"
      description="The password on this account, what stands in front of it, and what is currently signed in as you."
    >
      <PasswordSection />
      <SecondFactorSection />
      <ActiveSessionsSection />
      <SignOutEverywhereSection />
    </SettingsLayout>
  );
}
