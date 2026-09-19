import type { Action, Session } from "./identity";

/**
 * What `/auth/me` answers for a person with `role`, for screen tests.
 *
 * The permissions restate the API's `auth/permissions.py` table (F26, FR1), which
 * `test_policy.py` pins on the other side; a screen test that needs a viewer asks for
 * one here rather than writing its own list.
 */

export type TestRole = "owner" | "admin" | "member" | "viewer";

const WORK: Action[] = ["operate_checks", "operate_alerts", "upload_source_maps"];
const ADMINISTER: Action[] = [
  ...WORK,
  "manage_channels",
  "manage_check_secrets",
  "configure_projects",
  "manage_credentials",
  "manage_members",
  "read_audit",
];

export const PERMISSIONS_OF: Record<TestRole, Action[]> = {
  owner: [...ADMINISTER, "manage_owners", "grant_support", "commit_spend", "end_organization"],
  admin: ADMINISTER,
  member: WORK,
  viewer: [],
};

export function identityFor(role: TestRole = "owner"): Session {
  return {
    user: {
      id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0001",
      email: `${role}@example.test`,
      name: role,
      role,
      email_verified_at: "2026-09-01T09:15:00Z",
      permissions: PERMISSIONS_OF[role],
    },
    organization: { id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0002", slug: "acme", name: "Acme" },
  };
}
