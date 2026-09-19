import type { Member, Role } from "./api";

/**
 * What the signed-in person may do to whom — the API's `members/service.py`
 * rules (BR3), restated only so the screen offers what the API will accept. The
 * API refuses regardless.
 */

const RANK: Record<Role, number> = { viewer: 0, member: 1, admin: 2, owner: 3 };

export const ROLES: readonly { id: Role; label: string; says: string }[] = [
  {
    id: "owner",
    label: "Owner",
    says: "Everything, including billing and deleting the organization",
  },
  { id: "admin", label: "Admin", says: "Everything but billing, deletion and owners" },
  { id: "member", label: "Member", says: "Checks, alert rules and maintenance — the day-to-day" },
  { id: "viewer", label: "Viewer", says: "Reads every screen, changes nothing" },
];

/** At most the person's own role; an owner only from an owner. */
export function assignableRoles(own: Role): readonly { id: Role; label: string; says: string }[] {
  return ROLES.filter((role) => RANK[role.id] <= RANK[own]);
}

/** Nobody changes or removes someone whose role is above their own. */
export function mayActOn(own: Role, member: Member): boolean {
  return RANK[member.role] <= RANK[own];
}

export function roleLabel(role: Role): string {
  return ROLES.find((each) => each.id === role)?.label ?? role;
}

export function isRole(value: string): value is Role {
  return value in RANK;
}

export function displayName(member: Member): string {
  return member.name ?? member.email;
}
