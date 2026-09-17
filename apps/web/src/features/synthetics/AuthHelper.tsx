import { Link } from "@tanstack/react-router";

import type { AuthDraft, FieldProblems } from "./check-draft";
import type { AuthKind } from "./check-model";
import { InlineInput, InlineSelect } from "./fields";

const AUTH_KINDS: ReadonlyArray<{ value: AuthKind; label: string }> = [
  { value: "none", label: "No authentication" },
  { value: "basic", label: "Basic" },
  { value: "bearer", label: "Bearer token" },
  { value: "api_key", label: "API key" },
];

const NO_SECRET = "";

/**
 * Credentials are chosen from the project's secrets, never typed here (FR5, BR4): a
 * value typed into a check would be stored in the check, readable by everyone who can
 * read the project, and echoed into its evidence.
 */
export function AuthHelper({
  auth,
  onChange,
  problems,
  prefix,
  secretNames,
}: {
  auth: AuthDraft;
  onChange: (auth: AuthDraft) => void;
  problems: FieldProblems;
  prefix: string;
  secretNames: readonly string[];
}) {
  const secrets = [
    {
      value: NO_SECRET,
      label: secretNames.length ? "Choose a secret" : "No secrets in this project",
    },
    ...secretNames.map((name) => ({ value: name, label: name })),
  ];
  const at = (field: string) => problems[`${prefix}.auth.${field}`];

  return (
    <div className="flex max-w-xl flex-col gap-3">
      <InlineSelect
        label="Authentication"
        visibleLabel
        value={auth.kind}
        options={AUTH_KINDS}
        onChange={(kind) => onChange({ ...auth, kind })}
      />

      {auth.kind === "basic" ? (
        <>
          <InlineInput
            label="Username"
            visibleLabel
            value={auth.username}
            onChange={(username) => onChange({ ...auth, username })}
            error={at("username")}
          />
          <InlineSelect
            label="Password secret"
            visibleLabel
            value={auth.passwordSecret}
            options={secrets}
            onChange={(passwordSecret) => onChange({ ...auth, passwordSecret })}
            error={at("password_secret")}
          />
        </>
      ) : null}

      {auth.kind === "bearer" ? (
        <InlineSelect
          label="Token secret"
          visibleLabel
          value={auth.tokenSecret}
          options={secrets}
          onChange={(tokenSecret) => onChange({ ...auth, tokenSecret })}
          error={at("token_secret")}
        />
      ) : null}

      {auth.kind === "api_key" ? (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <InlineInput
              label="Name"
              visibleLabel
              mono
              value={auth.keyName}
              onChange={(keyName) => onChange({ ...auth, keyName })}
              error={at("name")}
            />
            <InlineSelect
              label="Sent in"
              visibleLabel
              value={auth.location}
              options={[
                { value: "header", label: "A header" },
                { value: "query", label: "The query string" },
              ]}
              onChange={(location) => onChange({ ...auth, location })}
            />
          </div>
          <InlineSelect
            label="Key secret"
            visibleLabel
            value={auth.valueSecret}
            options={secrets}
            onChange={(valueSecret) => onChange({ ...auth, valueSecret })}
            error={at("value_secret")}
          />
        </>
      ) : null}

      {auth.kind !== "none" ? (
        <p className="text-ink-dim text-xs">
          Secrets are written once and never shown again. Add or replace them in{" "}
          <Link
            to="/settings/projects"
            className="text-brand-text underline-offset-2 hover:underline"
          >
            project settings
          </Link>
          . Any secret can also be used as <code className="font-mono">{"{{secret.NAME}}"}</code> in
          the URL, a header or the body.
        </p>
      ) : null}
    </div>
  );
}
