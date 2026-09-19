import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { Button } from "@/shared/components/Button";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { formatTimestamp } from "@/shared/format/time";

import { AuthScreenLayout, FormMessage } from "./AuthScreenLayout";
import {
  acceptInvitation,
  PASSWORD_MIN_LENGTH,
  previewInvitation,
  sessionQueryKey,
  useSession,
} from "./api";
import { describeFormProblem, describeLinkRefusal } from "./problem-fields";

/**
 * Where an invite link lands (F26): who invited you, into what, as what — then a name and
 * a password, and you are in.
 *
 * It signs the person in, where a password reset deliberately does not: an invite has no
 * earlier session to protect, and landing inside the organization is the whole point of
 * accepting. The link is spent the moment the password is set.
 */
export function AcceptInviteScreen({ token }: { token?: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const current = useSession();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");

  const preview = useQuery({
    queryKey: ["invitation", token],
    queryFn: () => previewInvitation(token ?? ""),
    enabled: token !== undefined,
    retry: false,
  });

  const accept = useMutation({
    mutationKey: ["invitation", "accept"],
    mutationFn: acceptInvitation,
    onSuccess: async (session) => {
      queryClient.setQueryData(sessionQueryKey, session);
      // `replace` so Back cannot return to a form holding a spent link.
      await navigate({ to: "/", replace: true });
    },
  });
  const problem = describeFormProblem(accept.error);

  if (token === undefined) {
    return (
      <AuthScreenLayout
        title="This link is incomplete"
        intro="The address has no invite in it. Mail clients sometimes cut a long link in half — open it from the message again, or ask whoever invited you to resend it."
      />
    );
  }

  if (preview.isPending) {
    return (
      <AuthScreenLayout title="Opening your invite" intro="One moment.">
        <SkeletonRows rows={2} label="Reading the invite" />
      </AuthScreenLayout>
    );
  }

  if (preview.isError) {
    return (
      <AuthScreenLayout
        title="This invite cannot be used"
        intro={describeLinkRefusal(preview.error)}
        footer={
          <p>
            Already have a password?{" "}
            <Link to="/login" className="text-brand-text hover:underline">
              Sign in
            </Link>
            .
          </p>
        }
      />
    );
  }

  const invitation = preview.data;

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (token === undefined) return;
    accept.mutate({ token, name: name.trim(), password });
  }

  return (
    <AuthScreenLayout
      title={`Join ${invitation.organization}`}
      intro={`${invitation.invited_by} invited ${invitation.email} as ${article(invitation.role)} ${invitation.role}. The invite works until ${formatTimestamp(invitation.expires_at)}.`}
    >
      {current.data ? (
        // Accepting signs in as the invited address, replacing whoever is signed in.
        <p className="mb-4 text-ink-dim text-sm">
          You are signed in as {current.data.user.email}. Accepting signs you in as{" "}
          {invitation.email} instead.
        </p>
      ) : null}
      <form onSubmit={onSubmit} noValidate>
        {problem.message ? <FormMessage message={problem.message} /> : null}
        <div className="flex flex-col gap-4">
          <TextField
            label="Your name"
            name="name"
            value={name}
            onChange={setName}
            autoComplete="name"
            error={problem.fieldErrors.name}
          />
          <TextField
            label="Choose a password"
            name="password"
            type="password"
            value={password}
            onChange={setPassword}
            autoComplete="new-password"
            hint={`At least ${PASSWORD_MIN_LENGTH} characters.`}
            error={problem.fieldErrors.password}
          />
          <div className="mt-1">
            <Button type="submit" pending={accept.isPending} pendingLabel="Joining…">
              Join {invitation.organization}
            </Button>
          </div>
        </div>
      </form>
    </AuthScreenLayout>
  );
}

function article(role: string): string {
  return role[0] !== undefined && "aeiou".includes(role[0]) ? "an" : "a";
}
