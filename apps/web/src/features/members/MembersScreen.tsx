import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import { useCan, useSession } from "@/shared/identity";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";
import type { Member, Role } from "./api";
import {
  useChangeRole,
  useInvite,
  useMembers,
  useRemoveMember,
  useResendInvite,
  useWithdrawInvite,
} from "./api";
import { assignableRoles, displayName, isRole, mayActOn, roleLabel } from "./member-rules";

/**
 * Who is in this organization, and — for an owner or admin — the way to bring someone in,
 * change what they may do, or remove them (F26).
 *
 * Everyone may read the list: a teammate should be able to see who else is here. Only the
 * controls are held back, and they are held back by what the API would accept (BR3): a
 * role above your own is never offered, and nobody above you can be changed or removed.
 */
export function MembersScreen() {
  const members = useMembers();
  const session = useSession();
  const canManage = useCan("manage_members");
  const own = session.data?.user.role;
  const ownRole: Role | undefined = own !== undefined && isRole(own) ? own : undefined;

  return (
    <SettingsLayout
      title="Members"
      description="Everyone who can sign in to this organization, and every invite still waiting to be accepted. Each person signs in as themselves, with a role that decides what they can change."
    >
      {canManage && ownRole ? <InviteForm own={ownRole} /> : null}

      {members.isPending ? (
        <SkeletonRows rows={3} label="Loading members" />
      ) : members.isError ? (
        <InlineFailure
          title="Members could not be loaded"
          error={members.error}
          onRetry={() => void members.refetch()}
        />
      ) : (
        <ul className="border-line border-t" aria-label="Members">
          {members.data.map((member) => (
            <MemberRow
              key={member.id}
              member={member}
              isSelf={member.id === session.data?.user.id}
              own={canManage ? ownRole : undefined}
            />
          ))}
        </ul>
      )}
    </SettingsLayout>
  );
}

function InviteForm({ own }: { own: Role }) {
  const invite = useInvite();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [sent, setSent] = useState<Member>();
  const roles = assignableRoles(own);

  return (
    <section className="mb-6 border border-line bg-surface px-4 py-3" aria-label="Invite">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!email.trim()) return;
          invite.mutate(
            { email: email.trim(), role },
            {
              onSuccess: (member) => {
                setSent(member);
                setEmail("");
              },
            },
          );
        }}
      >
        <div className="min-w-64 flex-1">
          <TextField
            label="Invite by email"
            name="invite-email"
            type="email"
            value={email}
            onChange={setEmail}
            hint="They get a link to choose a password. It works once, for seven days."
          />
        </div>
        <div className="pb-2">
          <SelectField
            label="As"
            value={role}
            options={roles}
            onChange={(value) => {
              if (isRole(value)) setRole(value);
            }}
            optionLabel={(option) => option.label}
          />
        </div>
        <div className="pb-1">
          <Button type="submit" pending={invite.isPending} pendingLabel="Inviting…">
            Send invite
          </Button>
        </div>
      </form>
      <p className="mt-2 text-ink-dim text-xs">{roles.find((each) => each.id === role)?.says}.</p>
      {sent ? (
        <p role="status" className="mt-2 text-ink text-sm">
          {sent.invite_sent_at
            ? `Invited ${sent.email}. They have seven days to accept.`
            : `Invited ${sent.email}, but the email did not go. Use Resend on their row.`}
        </p>
      ) : null}
      {invite.error ? (
        <div className="mt-3">
          <InlineFailure title="The invite was not sent" error={invite.error} />
        </div>
      ) : null}
    </section>
  );
}

const STATUS_TONE: Record<Member["status"], string> = {
  active: "",
  invited: "border-info-border bg-info-subtle text-info-text",
  expired: "border-warning-border bg-warning-subtle text-warning-text",
};

function MemberRow({
  member,
  isSelf,
  own,
}: {
  member: Member;
  isSelf: boolean;
  /** The signed-in person's role when they may manage members; undefined otherwise. */
  own: Role | undefined;
}) {
  const changeRole = useChangeRole();
  const resend = useResendInvite();
  const withdraw = useWithdrawInvite();
  const [removing, setRemoving] = useState(false);
  const actionable = own !== undefined && mayActOn(own, member);
  const pending = member.status !== "active";
  const failure = changeRole.error ?? resend.error ?? withdraw.error;

  return (
    <li className="border-line-subtle border-b py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <div className="min-w-0">
          <p className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium text-base text-ink">{displayName(member)}</span>
            {member.name ? <span className="text-ink-dim text-sm">{member.email}</span> : null}
            {isSelf ? <span className="text-ink-dim text-xs">(you)</span> : null}
            {pending ? (
              <span
                className={`rounded-sm border px-1.5 py-0.5 font-mono text-2xs uppercase tracking-caps ${STATUS_TONE[member.status]}`}
              >
                {member.status === "invited" ? "Invited" : "Invite expired"}
              </span>
            ) : null}
          </p>
          <p className="mt-1 text-ink-dim text-sm">{describeWhen(member)}</p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {actionable && own ? (
            <SelectField
              label="Role"
              value={member.role}
              options={assignableRoles(own)}
              onChange={(value) => {
                if (isRole(value) && value !== member.role) {
                  changeRole.mutate({ memberId: member.id, role: value });
                }
              }}
              optionLabel={(option) => option.label}
            />
          ) : (
            <span className="text-ink text-sm">{roleLabel(member.role)}</span>
          )}
          {actionable && pending ? (
            <>
              <Button
                variant="secondary"
                pending={resend.isPending}
                pendingLabel="Sending…"
                onClick={() => resend.mutate(member.id)}
              >
                Resend
              </Button>
              <Button
                variant="secondary"
                pending={withdraw.isPending}
                pendingLabel="Withdrawing…"
                onClick={() => withdraw.mutate(member.id)}
              >
                Withdraw
              </Button>
            </>
          ) : null}
          {actionable && !pending && !removing ? (
            <Button variant="secondary" onClick={() => setRemoving(true)}>
              {isSelf ? "Leave" : "Remove"}
            </Button>
          ) : null}
        </div>
      </div>

      {failure ? (
        <div className="mt-3">
          <InlineFailure title="That did not work" error={failure} />
        </div>
      ) : null}
      {removing ? <RemoveConfirmation member={member} onDone={() => setRemoving(false)} /> : null}
    </li>
  );
}

/**
 * Removal signs the person out everywhere at once and cannot be undone — they would have
 * to be invited again — so it asks for their name typed out, the way deleting a project
 * does. What they made stays, and the confirmation says so.
 */
function RemoveConfirmation({ member, onDone }: { member: Member; onDone: () => void }) {
  const remove = useRemoveMember();
  const [typed, setTyped] = useState("");
  const name = displayName(member);

  return (
    <div className="mt-3 border-critical-solid border-l-2 bg-critical-subtle px-3 py-3">
      <p className="font-medium text-ink text-sm">Remove {name} from this organization?</p>
      <p className="mt-1 text-ink-dim text-sm leading-relaxed">
        They are signed out everywhere on their next click and cannot sign in again unless someone
        invites them back. The checks, rules and channels they made stay and keep working.
      </p>
      <div className="mt-3 max-w-sm">
        <TextField
          label={`Type ${name} to confirm`}
          name="remove-confirmation"
          value={typed}
          onChange={setTyped}
        />
      </div>
      {remove.error ? (
        <div className="mt-3">
          <InlineFailure title={`${name} was not removed`} error={remove.error} />
        </div>
      ) : null}
      <div className="mt-3 flex items-center gap-2">
        <Button
          disabled={typed.trim() !== name}
          pending={remove.isPending}
          pendingLabel="Removing…"
          onClick={() => remove.mutate(member.id, { onSuccess: onDone })}
        >
          Remove {name}
        </Button>
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function describeWhen(member: Member): string {
  if (member.status === "invited") {
    const by = member.invited_by ? ` by ${member.invited_by}` : "";
    const sent = member.invite_sent_at ? "" : " The email did not go — resend it.";
    const expires = member.invite_expires_at
      ? ` Expires ${formatRelativeTime(member.invite_expires_at)}.`
      : "";
    return `Invited${by}.${expires}${sent}`;
  }
  if (member.status === "expired") {
    return "The invite expired before it was accepted. Resend it for a new seven days.";
  }
  if (member.last_signed_in_at) {
    return `Last signed in ${formatRelativeTime(member.last_signed_in_at)} · added ${formatTimestamp(member.added_at)}`;
  }
  return `Added ${formatTimestamp(member.added_at)} · has not signed in yet`;
}
