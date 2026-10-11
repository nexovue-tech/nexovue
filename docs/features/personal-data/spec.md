# Feature: personal-data — export your data, and delete your account

## Status

Built — approved by the founder 2026-10-11; readiness group 9, D2
(`docs/changes/remaining-recommendations/design.md`), built as its T21. Two details changed in the build
and are recorded where they apply: deletion is `POST /profile/deletion`, not `DELETE /profile`, and the
confirm button is the house's primary button in a red-edged panel.

## Problem

A person using nexovue cannot see what nexovue holds about them, and cannot leave on their own. Only an
admin can remove them, and even then their email address stays in the audit log for good. GDPR (and
India's DPDP Act) give every person both rights; a SaaS that cannot honour them on request is not
ready to sell in the EU or to an EU customer's staff.

## Goal

Any signed-in person can download everything nexovue holds about them as a person, and can delete
their own account, from the Account settings screen, without asking anyone.

## Target users

Every member of every organization, whatever their role. **Not** the organization as a whole:
deleting a company's data is `DELETE /organization` (ADR-014), which already exists. **Not** the
platform operator, who has no customer data of their own here.

## Scope

- Export of a person's own data.
- Deletion of a person's own account.
- On removal by an admin, the same scrubbing a self-deletion does.

## Out of scope

- Exporting the organization's telemetry — that is the organization's data, not the person's, and the
  console's exports already exist.
- Deleting an organization (ADR-014).
- A waiting period or an undo: a deleted account is gone at once, as a removed member is today.

## User requirements

1. *"I can download what nexovue knows about me."*
2. *"I can delete my account myself."*
3. *"If I am the only owner, I am told what to do instead, not left with a broken company."*

## Business rules

- **Export holds the person's data only**: their profile; their sessions (device and network, when and
  where); the audit entries they caused; the API tokens they created; the invitations they sent and
  received. Never another person's data, never the organization's telemetry.
- **Deleting an account is the member removal that already exists** (sessions ended, email freed,
  set-password state cleared), plus: their **name becomes "Former member"**, and **their email is
  replaced by their user id in every audit entry**. The audit entries stay: they are the
  organization's record of what happened, now without the person in them.
- **The organization's last owner cannot delete their account.** They are told to transfer ownership
  or delete the organization.
- **An admin removing someone scrubs the same way**, so removal is never kinder to history than
  self-deletion.
- API tokens a person created stay the organization's (ADR-017, BR9); the export lists them.

## Functional requirements

- `GET /api/v1/profile/data.json` — one JSON file, `Content-Disposition: attachment`, for the signed-in
  person. Signed-in only, any role. Recorded in the audit log.
- `POST /api/v1/profile/deletion` — requires the current password (and a code when two-factor is on), so
  a forgotten session cannot delete an account. Ends every session and clears the cookie. *A POST to a
  named action, not `DELETE /profile`: it carries the password, and a body on DELETE has no defined
  meaning (RFC 9110 §9.3.5).*
- `GET /api/v1/profile/deletion` — whether the person may delete their account, with the refusal's
  sentence when not, so the screen tells the last owner before they type a password.
- Both refuse an API token: they are a person's acts. The export also refuses a support view: reading
  as the person is not the person taking their file.

## Non-functional requirements

- The export answers within a few seconds for a person with years of audit entries (bounded at 10,000
  entries, newest first, with a note in the file when cut).

## Error cases

| Case | What the person sees |
|---|---|
| Wrong password or code on delete | "That password is not right" — nothing deleted |
| Last owner | "You are this organization's only owner. Make someone else an owner, or delete the organization." |
| Export larger than the bound | The file, with `"truncated": true` and the date of the oldest entry it holds |

## UI

On **Settings → Account**, below *Sign out everywhere*, one section **Your data**:

```
Your data
──────────────────────────────────────────────────────────────
Download your data                                [ Download ]
Everything nexovue holds about you as a person: your profile,
sessions, what you did, and the tokens you made.

Delete your account                       [ Delete account… ]
Removes you from <Organization>. What you did stays in the
audit log, without your name or address.
```

**Delete account…** opens a confirmation: the password field (and a code when two-factor is on), the
sentence *"This cannot be undone."*, and a red **Delete my account** button. On success the person is
signed out and lands on the sign-in screen with *"Your account was deleted."* For the last owner the
button is replaced by the sentence in *Error cases* and a link to Members.

## Acceptance criteria

1. A person downloads a JSON file holding exactly the categories in *Business rules*, and nothing of
   another person's.
2. A person deletes their account with their password; they are signed out, cannot sign in again, their
   address can sign up anew, and every audit entry they caused names their user id and no email.
3. The last owner is refused with the sentence above, and nothing changes.
4. An admin removing a member leaves the same audit result as 2.
5. An API token is refused on both routes.
