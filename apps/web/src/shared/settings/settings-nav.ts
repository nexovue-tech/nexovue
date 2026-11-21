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
  | "/settings/api-tokens"
  | "/settings/members"
  | "/settings/audit"
  | "/settings/support"
  | "/settings/channels"
  | "/settings/ai"
  | "/settings/runbooks"
  | "/settings/explanations"
  | "/settings/connections"
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
   * Only on nexovue's hosted service (`shared/deployment.ts`): a customer's own install has
   * no billing and no nexovue staff to grant support access to.
   */
  hostedOnly?: true;
  /**
   * Only where AI may be offered, a customer's own install (F4 `ai_available`): runbooks,
   * which chat quotes. The hosted service offers no chat yet.
   */
  selfHostedOnly?: true;
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
        keywords: ["api", "key", "secret", "site key", "browser key", "otlp", "write"],
      },
      {
        path: "/settings/api-tokens",
        title: "API tokens",
        keywords: ["api", "token", "read", "ci", "script", "mcp", "assistant", "curl", "export"],
      },
      {
        path: "/settings/audit",
        title: "Audit log",
        requires: "read_audit",
        keywords: ["audit", "history", "activity", "who", "log", "security", "support"],
      },
      {
        path: "/settings/support",
        title: "Support access",
        requires: "grant_support",
        hostedOnly: true,
        keywords: ["support", "help", "view as", "impersonate", "access", "nexovue"],
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
        // F3: open to everyone, since its first sentence answers "is AI on here?"; owners
        // and admins set it up there (design, *AI settings*).
        path: "/settings/ai",
        title: "AI wording",
        keywords: [
          "ai",
          "model",
          "provider",
          "ollama",
          "claude",
          "openai",
          "bedrock",
          "limit",
          "tokens",
        ],
      },
      {
        // F4 T5.3 (D40): its own screen, not inside AI settings, since members write
        // runbooks and AI settings are for those who manage AI. Everyone signed in reads.
        path: "/settings/runbooks",
        title: "Runbooks",
        selfHostedOnly: true,
        keywords: ["runbook", "playbook", "procedure", "instructions", "chat", "knowledge"],
      },
      {
        // F3 FR54: rating and rule counts, and stopping a rule naming causes. Not an AI
        // screen: it stands with AI off.
        path: "/settings/explanations",
        title: "Explanations",
        requires: "configure_detection",
        keywords: ["explanation", "likely cause", "rule", "rating", "useful", "evidence"],
      },
      {
        path: "/settings/connections",
        title: "Connections",
        // F22: GitHub (and, once Atlassian answers, Jira, which nothing names until then).
        requires: "manage_connections",
        keywords: ["github", "issues", "tracker", "app", "repository", "integration"],
      },
      {
        path: "/settings/projects",
        title: "Projects",
        keywords: ["project", "environment", "staging", "production", "delete"],
      },
      {
        path: "/settings/billing",
        title: "Billing",
        hostedOnly: true,
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

/**
 * What the rail offers this reader: what they may open, on this kind of install, matching
 * what they typed (lower-cased). Groups left empty are dropped.
 */
export function offeredSettings(
  groups: readonly SettingsGroup[],
  {
    permissions,
    hosted,
    needle,
  }: { permissions: readonly Action[] | undefined; hosted: boolean; needle: string },
): SettingsGroup[] {
  return groups
    .map((group) => ({
      ...group,
      items: group.items.filter(
        (item) =>
          (item.requires === undefined || permissions?.includes(item.requires) === true) &&
          (item.hostedOnly === undefined || hosted) &&
          (item.selfHostedOnly === undefined || !hosted) &&
          (needle === "" ||
            item.title.toLowerCase().includes(needle) ||
            item.keywords.some((word) => word.includes(needle))),
      ),
    }))
    .filter((group) => group.items.length > 0);
}
