import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { CopyButton } from "@/shared/components/CopyButton";
import { Permitted } from "@/shared/components/Permitted";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { useNothingLive } from "@/shared/refresh";
import { useSelection } from "@/shared/selection";

import type { CreatedCheck } from "./api";
import { useCreateCheck } from "./api";
import { CheckForm } from "./CheckForm";
import { emptyDraft } from "./check-draft";
import type { CheckType } from "./check-model";
import { CHECK_TYPES } from "./check-model";

export const KIND_DESCRIPTIONS: Record<CheckType, { title: string; detail: string }> = {
  api: {
    title: "API",
    detail:
      "One request — any method, headers, body and files — and what a healthy response looks like.",
  },
  multistep: {
    title: "Multistep API",
    detail: "Requests in order, passing values between them: log in, create an order, fetch it.",
  },
  heartbeat: {
    title: "Heartbeat",
    detail: "Your cron job calls a URL when it succeeds. Silence is the failure.",
  },
  tcp: { title: "TCP", detail: "A host accepts connections on a port." },
  dns: { title: "DNS", detail: "A name resolves, optionally to the answer you expect." },
};

/**
 * Choose a kind, then describe the check (design, *User experience → Create*). The
 * simplest check — "is this URL up" — is a URL and nothing else: an API check starts
 * with a status assertion already written.
 */
export function NewCheckScreen() {
  return (
    <Permitted action="operate_checks" who="an owner, an admin or a member">
      <NewCheckScreenForm />
    </Permitted>
  );
}

function NewCheckScreenForm() {
  // A form reads nothing on a timer: the top bar's interval control would govern
  // nothing here, and reads as a promise that this screen is watching something.
  useNothingLive();
  const { projectId } = useSelection();
  const navigate = useNavigate();
  const { kind } = useSearch({ from: "/shell/synthetics/new" });
  const create = useCreateCheck(projectId);
  const [created, setCreated] = useState<CreatedCheck>();

  useEffect(() => {
    // The form is gone by now, so its unsaved-changes guard is too.
    if (created && !created.heartbeat_token) {
      void navigate({ to: "/synthetics/$checkId", params: { checkId: created.id } });
    }
  }, [created, navigate]);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
          <Link to="/synthetics" className="hover:text-ink">
            Synthetics
          </Link>{" "}
          / New check
        </p>
        <h1 className="mt-1 font-semibold font-serif text-ink text-xl tracking-tight">
          {kind ? `New ${KIND_DESCRIPTIONS[kind].title} check` : "What should this check watch?"}
        </h1>
      </header>

      <div className="min-w-0 px-4 pb-10">
        {projectId === undefined ? (
          // A check saved before the project is known would be saved nowhere.
          <SkeletonRows rows={4} label="Loading the project" />
        ) : created?.heartbeat_token ? (
          <HeartbeatCreated check={created} token={created.heartbeat_token} />
        ) : created ? null : kind ? (
          <CheckForm
            key={kind}
            projectId={projectId}
            initial={emptyDraft(kind)}
            submitLabel="Create check"
            pendingLabel="Creating"
            save={{
              mutate: (input, options) => create.mutate(input, options),
              isPending: create.isPending,
              error: create.error,
              reset: create.reset,
            }}
            onSaved={(saved) => setCreated(saved as CreatedCheck)}
          />
        ) : (
          <KindChooser />
        )}
      </div>
    </div>
  );
}

function KindChooser() {
  // Five kinds: two columns orphan the fifth on a row of its own, inside a block
  // two thirds the width of the screen. Three seats them 3 + 2
  // (`docs/quality/console-review-2026-09-20.md`).
  return (
    <ul className="grid max-w-5xl grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {CHECK_TYPES.map((kind) => (
        <li key={kind}>
          <Link
            to="/synthetics/new"
            search={{ kind }}
            className="flex h-full flex-col gap-1 rounded-sm border border-line px-3 py-2.5 hover:border-line-strong hover:bg-hover focus-visible:outline-2 focus-visible:outline-brand"
          >
            <span className="font-medium text-ink text-sm">{KIND_DESCRIPTIONS[kind].title}</span>
            <span className="text-ink-dim text-sm">{KIND_DESCRIPTIONS[kind].detail}</span>
          </Link>
        </li>
      ))}
      <li className="sm:col-span-2">
        <p className="text-ink-dim text-sm">
          Already have a cURL command, an OpenAPI description or a Postman collection?{" "}
          <Link to="/synthetics/import" className="text-brand-text hover:underline">
            Import it
          </Link>
          .
        </p>
      </li>
    </ul>
  );
}

/** Shown once: only its hash is kept, so there is no second chance to copy it (FR16). */
function HeartbeatCreated({ check, token }: { check: CreatedCheck; token: string }) {
  const url = `${window.location.origin}/api/v1/heartbeat/${token}`;
  return (
    <section className="flex max-w-2xl flex-col gap-3" aria-label="Heartbeat created">
      <p className="text-ink text-sm">
        <span className="font-medium">{check.name}</span> is created. Call this URL at the end of
        every successful run of your job. <strong>Copy it now — it is not shown again.</strong> If
        it is lost, issue a new one from the check page; the old one stops working.
      </p>
      <div className="flex min-w-0 items-center gap-2 rounded-sm border border-line bg-surface px-2 py-1.5">
        <code className="min-w-0 flex-1 break-all font-mono text-ink text-xs">{url}</code>
        <CopyButton value={url} label="heartbeat URL" />
      </div>
      <pre className="overflow-x-auto rounded-sm bg-raised px-2 py-1.5 font-mono text-ink text-xs">
        {`curl -fsS -X POST ${url}`}
      </pre>
      <div>
        <Link
          to="/synthetics/$checkId"
          params={{ checkId: check.id }}
          className="text-brand-text text-sm hover:underline"
        >
          Open the check
        </Link>
      </div>
    </section>
  );
}
