import type { Action } from "../identity";

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
  | "/settings/members"
  | "/settings/audit"
  | "/settings/channels"
  | "/settings/projects"
  | "/settings/billing"
  | "/settings/usage";

export interface SettingsItem {
  path: SettingsPath;
  title: string;
  /**
   * Shown only to people who may do this. Absent means every signed-in person may
   * open the screen — most settings are readable by anyone in the organization.
   */
  requires?: Action;
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
        // Not "theme": it is a control in the sidebar footer now, on every screen,
        // and a rail entry leading to a page that no longer holds it is worse than
        // no match at all.
        keywords: ["name", "profile", "email", "address"],
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
        path: "/settings/members",
        title: "Members",
        keywords: ["team", "invite", "people", "users", "role", "owner", "admin", "remove"],
      },
      {
        path: "/settings/api-keys",
        title: "Ingest keys",
        keywords: ["api", "key", "token", "secret", "site key", "browser key", "otlp"],
      },
      {
        path: "/settings/audit",
        title: "Audit log",
        requires: "read_audit",
        keywords: ["audit", "history", "activity", "who", "log", "security", "support"],
      },
      {
        path: "/settings/channels",
        title: "Alert channels",
        keywords: [
          "alert",
          "notification",
          "slack",
          "teams",
          "discord",
          "pagerduty",
          "webhook",
          "email",
          "on-call",
        ],
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
