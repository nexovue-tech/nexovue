import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { Refused } from "@/shared/components/Refused";
import { TextField } from "@/shared/components/TextField";
import { useCan, useSession } from "@/shared/identity";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";

import { useStartGitHub } from "./api";
import { startSentence } from "./connection-words";

/**
 * The full-page start of GitHub's manifest flow (F22 T3.9, design *Connecting GitHub*). This
 * page is the one route whose policy lets a form post to github.com (`Caddyfile`), so it is
 * reached by a full page load, never an in-app link. It asks the API for the manifest and a
 * one-time `state`, then posts the form GitHub's manifest page describes.
 *
 * `submit` exists for tests: jsdom does not submit forms.
 */
export function GitHubStartScreen({
  submit = (form: HTMLFormElement) => form.submit(),
}: {
  submit?: (form: HTMLFormElement) => void;
}) {
  const allowed = useCan("manage_connections");
  const session = useSession();
  const start = useStartGitHub();
  const [reachable, setReachable] = useState(false);
  const [organization, setOrganization] = useState("");

  const go = () =>
    start.mutate(
      { reachable, github_organization: organization.trim() || null },
      {
        onSuccess: (started) =>
          submit(manifestForm(started.post_url, started.state, started.manifest)),
      },
    );

  return (
    <SettingsLayout
      title="Connect GitHub"
      description="Create this organization's own private App."
    >
      {!allowed ? (
        <Refused role={session.data?.user.role} who="an owner or an admin" />
      ) : (
        <div className="flex max-w-prose flex-col gap-4">
          <p className="text-ink text-sm">
            {startSentence(session.data?.organization.slug ?? "your-organization")}
          </p>
          <fieldset className="flex flex-col gap-1.5">
            <legend className="text-ink text-sm">
              Can GitHub reach this server from the internet?
            </legend>
            {[
              { value: "no", label: "No: nexovue reads GitHub on a timer" },
              { value: "yes", label: "Yes: GitHub may also tell nexovue at once" },
            ].map((option) => (
              <label key={option.value} className="flex items-center gap-2 text-ink text-sm">
                <input
                  type="radio"
                  name="reachable"
                  value={option.value}
                  checked={reachable === (option.value === "yes")}
                  onChange={() => setReachable(option.value === "yes")}
                />
                {option.label}
              </label>
            ))}
          </fieldset>
          <TextField
            label="GitHub organization"
            name="github_organization"
            value={organization}
            onChange={setOrganization}
            required={false}
            hint="Its name as in github.com/<name>. Leave empty to create the App on your own account."
          />
          <div>
            <Button pending={start.isPending} pendingLabel="Preparing…" onClick={go}>
              Continue to GitHub
            </Button>
          </div>
          {start.error ? <InlineFailure title="Setup did not start" error={start.error} /> : null}
        </div>
      )}
    </SettingsLayout>
  );
}

/** The form GitHub's manifest flow takes: the manifest as one field, the state in the address. */
function manifestForm(
  postUrl: string,
  state: string,
  manifest: Record<string, unknown>,
): HTMLFormElement {
  const form = document.createElement("form");
  form.method = "post";
  form.action = `${postUrl}?state=${encodeURIComponent(state)}`;
  const field = document.createElement("input");
  field.type = "hidden";
  field.name = "manifest";
  field.value = JSON.stringify(manifest);
  form.append(field);
  document.body.append(form);
  return form;
}
