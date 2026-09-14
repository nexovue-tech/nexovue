/**
 * What the settings rail offers, and what each entry can be found by.
 *
 * Separate from the layout so the manifest test can read it without rendering,
 * the same way `route-manifest.ts` is separate from the sidebar.
 */

export type SettingsPath =
  | "/settings/general"
  | "/settings/account"
  | "/settings/api-keys"
  | "/settings/projects"
  | "/settings/billing"
  | "/settings/usage";

export interface SettingsItem {
  path: SettingsPath;
  title: string;
  /**
   * What somebody might type looking for it, in the words they would use rather
   * than the ones on the tab. "Sessions" and "two-factor" both live under
   * Account, and nobody looking for either would guess that.
   */
  keywords: readonly string[];
}

export interface SettingsGroup {
  label: string;
  items: readonly SettingsItem[];
}

export const SETTINGS_GROUPS: readonly SettingsGroup[] = [
  {
    label: "You",
    items: [
      {
        path: "/settings/general",
        title: "General",
        keywords: ["name", "theme", "appearance", "dark", "light", "profile"],
      },
      {
        path: "/settings/account",
        title: "Account",
        keywords: [
          "password",
          "sessions",
          "devices",
          "two-factor",
          "2fa",
          "totp",
          "security",
          "sign out",
        ],
      },
    ],
  },
  {
    label: "This organization",
    items: [
      {
        path: "/settings/api-keys",
        title: "Ingest keys",
        keywords: ["api", "key", "token", "secret", "site key", "browser key", "otlp"],
      },
      {
        path: "/settings/projects",
        title: "Projects",
        keywords: ["project", "environment", "staging", "production", "delete"],
      },
      {
        path: "/settings/billing",
        title: "Billing",
        keywords: ["plan", "payment", "card", "invoice", "charge", "subscription", "cancel"],
      },
      {
        path: "/settings/usage",
        title: "Usage",
        keywords: ["ingestion", "bytes", "allowance", "quota", "spend"],
      },
    ],
  },
];
