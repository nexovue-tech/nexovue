import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";

import { isConflict } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { CopyButton } from "@/shared/components/CopyButton";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { formatDate, formatTimestamp } from "@/shared/format/time";
import { useCan, useSession } from "@/shared/identity";
import { SettingsLayout } from "@/shared/settings/SettingsLayout";
import {
  BEAT_LOCK_EXPOSED,
  CONTAINER_NOT_RUNNING,
  checkLine,
  costLine,
  eraseWarning,
  FORMAT_WORDS,
  GATEWAY_LINE,
  LIMIT_GUESS,
  MCP_LINE,
  MEMBER_LINE,
  MEMBER_ON_LINE,
  MISSING_ANCHOR,
  MODEL_CHANGE_NOTE,
  MODEL_CHANGED,
  missingLine,
  NO_ANSWER,
  NO_FORMAT_YET,
  NO_RUNS,
  NOT_YET_TESTED_BY_NEXOVUE,
  OWNER_LEFT,
  outcomeLine,
  SELLING_LINE,
  statusLine,
  tokens,
  unavailableLine,
  usageLine,
  usedShare,
  whereDataGoes,
} from "./ai-words";
import {
  type AISettings,
  CHECK_ANSWER_MS,
  type ConnectionView,
  type RunRow,
  type SaveConnection,
  useAISettings,
  useCheckResult,
  useConsent,
  useDeleteConnection,
  useOptOuts,
  usePreview,
  useRuns,
  useSaveConnection,
  useSent,
  useSetOptOut,
  useStartCheck,
  useSwitch,
  useUsage,
  type WireFormat,
} from "./api";

/**
 * `/settings/ai` (F3 FR23 to FR32; design *AI settings*): connect a provider, test it, set a
 * limit, read the exact bytes, an owner's consent, the switch, project opt-outs, usage and
 * runs. Owners and admins see all of it; members and viewers read one sentence (AC85). A
 * failed read shows `InlineFailure` and no switch, so it never reads as "off" (AC90).
 */
export function AISettingsScreen() {
  const settings = useAISettings();
  return (
    <SettingsLayout
      title="AI wording"
      description="Optional plainer wording for a finding's facts, written by a model you choose. Off until an owner agrees to exactly what is sent."
    >
      {settings.isPending ? (
        <SkeletonRows rows={5} label="Loading the AI settings" />
      ) : settings.isError ? (
        <InlineFailure
          title="The AI settings could not be loaded"
          error={settings.error}
          onRetry={() => void settings.refetch()}
        />
      ) : settings.data.full ? (
        <Managed settings={settings.data} />
      ) : (
        <div className="flex flex-col gap-3 text-sm">
          <p role="status" className="max-w-prose text-ink">
            {settings.data.on ? MEMBER_ON_LINE : MEMBER_LINE}
          </p>
          <p className="max-w-prose text-ink-dim">{MCP_LINE}</p>
        </div>
      )}
    </SettingsLayout>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      className="flex flex-col gap-3 border-line border-t pt-4"
    >
      <h2 id={`${id}-title`} className="font-medium font-serif text-base text-ink">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Managed({ settings }: { settings: AISettings }) {
  const connection = settings.connection ?? null;
  const usable = settings.available === true;
  return (
    <div className="flex min-w-0 flex-col gap-6 text-sm">
      <Status settings={settings} />
      {usable ? (
        <>
          <Provider settings={settings} connection={connection} />
          {connection ? (
            <>
              <TestSection settings={settings} connection={connection} />
              <PreviewSection />
              <ConsentSection settings={settings} connection={connection} />
              <SwitchSection settings={settings} />
              <ProjectsSection />
              <UsageSection />
            </>
          ) : null}
        </>
      ) : null}
      <Section id="ai-not-covered" title="What this does not cover">
        <p className="max-w-prose text-ink-dim">{MCP_LINE}</p>
      </Section>
    </div>
  );
}

function Status({ settings }: { settings: AISettings }) {
  const connection = settings.connection;
  return (
    <section id="ai-status" aria-label="Status" className="flex flex-col gap-2">
      <p role="status" className="font-medium text-ink">
        {settings.unavailable_reason
          ? unavailableLine(settings.unavailable_reason)
          : statusLine(settings)}
      </p>
      {settings.available && settings.container_running === false ? (
        <p className="text-ink">{CONTAINER_NOT_RUNNING}</p>
      ) : null}
      {settings.beat_lock_exposed ? (
        <p className="max-w-prose text-warning-text">{BEAT_LOCK_EXPOSED}</p>
      ) : null}
      {connection?.model_changed ? <p className="text-ink">{MODEL_CHANGED}</p> : null}
      {connection?.consent?.by_left ? <p className="text-ink">{OWNER_LEFT}</p> : null}
      {settings.available && !settings.on && (settings.missing ?? []).length > 0 ? (
        <ul aria-label="Still needed" className="flex list-disc flex-col gap-1 pl-5 text-ink-dim">
          {(settings.missing ?? []).map((missing) => (
            <li key={missing}>
              <a className="underline" href={`#${MISSING_ANCHOR[missing]}`}>
                {missingLine(missing)}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="max-w-prose text-ink-dim">{SELLING_LINE}</p>
    </section>
  );
}

// ------------------------------------------------------------------ provider

interface Draft {
  wire_format: WireFormat | "";
  base_url: string;
  model: string;
  region_label: string;
  key: string;
  call_seconds: string;
  monthly_token_limit: string;
  price_input: string;
  price_output: string;
}

function draftOf(connection: ConnectionView | null, settings: AISettings): Draft {
  return {
    wire_format: connection?.wire_format ?? settings.formats_offered?.[0] ?? "",
    base_url: connection?.base_url ?? "",
    model: connection?.model ?? "",
    region_label: connection?.region_label ?? "",
    key: "",
    call_seconds: String(connection?.call_seconds ?? 100),
    monthly_token_limit: connection?.monthly_token_limit
      ? String(connection.monthly_token_limit)
      : "",
    price_input: connection?.price_input ?? "",
    price_output: connection?.price_output ?? "",
  };
}

function sameDraft(a: Draft, b: Draft): boolean {
  return (Object.keys(a) as (keyof Draft)[]).every((name) => a[name] === b[name]);
}

function toRequest(draft: Draft): SaveConnection | string {
  const limit = draft.monthly_token_limit.trim();
  if (limit !== "" && !/^[1-9][0-9]*$/.test(limit))
    return "The monthly limit must be a whole number of tokens.";
  const seconds = Number(draft.call_seconds);
  if (!Number.isInteger(seconds) || seconds < 30 || seconds > 300)
    return "The time per call must be 30 to 300 seconds.";
  if (draft.wire_format === "") return NO_FORMAT_YET;
  return {
    wire_format: draft.wire_format,
    base_url: draft.base_url.trim(),
    model: draft.model.trim(),
    region_label: draft.region_label.trim() || null,
    ...(draft.key ? { key: draft.key } : {}),
    call_seconds: seconds,
    monthly_token_limit: limit === "" ? null : Number(limit),
    price_input: draft.price_input.trim() || null,
    price_output: draft.price_output.trim() || null,
  };
}

function Provider({
  settings,
  connection,
}: {
  settings: AISettings;
  connection: ConnectionView | null;
}) {
  const saved = draftOf(connection, settings);
  const [draft, setDraft] = useState<Draft>(saved);
  const [confirming, setConfirming] = useState<"erase" | "remove" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const save = useSaveConnection();
  const remove = useDeleteConnection();
  const session = useSession();
  const organization = session.data?.organization.name ?? "this organization";
  const dirty = !sameDraft(draft, saved);
  const guard = useUnsavedChanges(dirty);
  const offered = [
    ...new Set([
      ...(settings.formats_offered ?? []),
      ...(connection ? [connection.wire_format] : []),
    ]),
  ];
  const addressMoved =
    connection !== null &&
    (draft.base_url.trim() !== connection.base_url || draft.wire_format !== connection.wire_format);
  const erases = addressMoved && connection.has_key && draft.key === "";
  const modelMoved = connection !== null && draft.model.trim() !== connection.model;
  const set = (name: keyof Draft) => (value: string) => setDraft({ ...draft, [name]: value });

  const submit = () => {
    const request = toRequest(draft);
    if (typeof request === "string") {
      setProblem(request);
      return;
    }
    setProblem(null);
    save.mutate(request, {
      onSuccess: (next) => {
        setConfirming(null);
        setDraft(draftOf(next.connection ?? null, next));
      },
    });
  };

  return (
    <Section id="ai-provider" title="Provider">
      <UnsavedChangesPrompt guard={guard} what="the provider connection" />
      {offered.length === 0 ? <p className="max-w-prose text-ink">{NO_FORMAT_YET}</p> : null}
      {connection ? <p className="max-w-prose text-ink-dim">{whereDataGoes(connection)}</p> : null}
      <form
        className="grid min-w-0 gap-3 md:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (erases) setConfirming("erase");
          else submit();
        }}
      >
        <SelectField
          stacked
          label="Wire format"
          value={draft.wire_format}
          options={offered.map((id) => ({ id }))}
          optionLabel={(option) => FORMAT_WORDS[option.id as WireFormat]}
          onChange={(id) => set("wire_format")(id)}
        />
        <TextField
          label="Base URL"
          name="base_url"
          value={draft.base_url}
          onChange={set("base_url")}
          hint="https, or a private address the operator listed."
        />
        <TextField
          label="Model"
          name="model"
          value={draft.model}
          onChange={set("model")}
          hint={modelMoved ? MODEL_CHANGE_NOTE : undefined}
        />
        <TextField
          label="Region, as you call it"
          name="region_label"
          required={false}
          value={draft.region_label}
          onChange={set("region_label")}
        />
        <TextField
          label="Key"
          name="key"
          type="password"
          autoComplete="off"
          required={false}
          value={draft.key}
          onChange={set("key")}
          hint={
            connection?.has_key
              ? "A key is saved and never shown. Leave this empty to keep it."
              : "Empty for a local server with none."
          }
        />
        <TextField
          label="Seconds per call"
          name="call_seconds"
          value={draft.call_seconds}
          onChange={set("call_seconds")}
        />
        <TextField
          label="Monthly token limit"
          name="monthly_token_limit"
          required={false}
          value={draft.monthly_token_limit}
          onChange={set("monthly_token_limit")}
          hint={LIMIT_GUESS}
        />
        <TextField
          label="Price per million input tokens (optional)"
          name="price_input"
          required={false}
          value={draft.price_input}
          onChange={set("price_input")}
        />
        <TextField
          label="Price per million output tokens (optional)"
          name="price_output"
          required={false}
          value={draft.price_output}
          onChange={set("price_output")}
        />
        <div className="flex flex-wrap items-end gap-2 md:col-span-2">
          <Button
            type="submit"
            pending={save.isPending}
            pendingLabel="Saving"
            disabled={!dirty || offered.length === 0}
          >
            Save the connection
          </Button>
          {connection ? (
            <Button variant="secondary" onClick={() => setConfirming("remove")}>
              Remove this connection
            </Button>
          ) : null}
        </div>
      </form>
      {confirming === "erase" ? (
        <ConfirmPanel label="Save a new address">
          <p className="max-w-prose">{eraseWarning(organization)}</p>
          <div className="flex flex-wrap gap-2">
            <Button pending={save.isPending} onClick={submit}>
              Save and erase the key
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
          </div>
        </ConfirmPanel>
      ) : null}
      {confirming === "remove" ? (
        <ConfirmPanel label="Remove this connection">
          <p className="max-w-prose">{eraseWarning(organization)}</p>
          <div className="flex flex-wrap gap-2">
            <Button
              pending={remove.isPending}
              onClick={() => remove.mutate(undefined, { onSuccess: () => setConfirming(null) })}
            >
              Remove the connection
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
          </div>
        </ConfirmPanel>
      ) : null}
      {problem ? <p className="text-critical-text">{problem}</p> : null}
      {save.error ? (
        <InlineFailure title="The connection was not saved" error={save.error} />
      ) : null}
      {remove.error ? (
        <InlineFailure title="The connection was not removed" error={remove.error} />
      ) : null}
    </Section>
  );
}

// ---------------------------------------------------------------------- test

function TestSection({
  settings,
  connection,
}: {
  settings: AISettings;
  connection: ConnectionView;
}) {
  const start = useStartCheck();
  const [requestId, setRequestId] = useState<string | null>(null);
  const [waitedTooLong, setWaitedTooLong] = useState(false);
  const result = useCheckResult(requestId);
  const pending = requestId !== null && (result.data?.pending ?? true) && !result.isError;

  useEffect(() => {
    if (!pending) return;
    const timer = setTimeout(() => setWaitedTooLong(true), CHECK_ANSWER_MS);
    return () => clearTimeout(timer);
  }, [pending]);

  return (
    <Section id="ai-test" title="Test">
      <p className="max-w-prose text-ink-dim">{NOT_YET_TESTED_BY_NEXOVUE}</p>
      <div>
        <Button
          pending={start.isPending || (pending && !waitedTooLong)}
          pendingLabel="Testing"
          disabled={settings.container_running === false}
          onClick={() => {
            setWaitedTooLong(false);
            start.mutate(undefined, { onSuccess: (accepted) => setRequestId(accepted.request_id) });
          }}
        >
          Test this connection
        </Button>
      </div>
      <p role="status" className="text-ink">
        {pending
          ? waitedTooLong
            ? NO_ANSWER
            : "Waiting for the AI container."
          : result.data && !result.data.pending
            ? checkLine(result.data.ok === true, result.data.reason)
            : connection.checked_at
              ? `Last test ${formatTimestamp(connection.checked_at)}: ${connection.checked_ok ? "passed" : "did not pass"}${connection.test_current ? "" : ", for values since changed"}.`
              : "Not tested yet."}
      </p>
      {start.error ? <InlineFailure title="The test could not start" error={start.error} /> : null}
      {result.error ? (
        <InlineFailure title="The test result could not be read" error={result.error} />
      ) : null}
    </Section>
  );
}

// ------------------------------------------------------------------- preview

function PreviewSection() {
  const preview = usePreview(true);
  return (
    <Section id="ai-preview" title="What would be sent">
      {preview.isPending ? (
        <SkeletonRows rows={3} label="Loading the preview" />
      ) : preview.isError ? (
        <InlineFailure
          title="The preview could not be loaded"
          error={preview.error}
          onRetry={() => void preview.refetch()}
        />
      ) : (
        <div className="flex min-w-0 flex-col gap-3">
          {preview.data.sample ? (
            <p className="font-medium text-ink">A built-in sample, not a finding of yours.</p>
          ) : null}
          <ul className="flex max-w-prose list-disc flex-col gap-1 pl-5 text-ink">
            {preview.data.summary.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <div className="flex min-w-0 flex-col gap-3 md:flex-row-reverse">
            <dl
              aria-label="Stand-in names"
              className="grid shrink-0 grid-cols-[auto_1fr] gap-x-3 gap-y-1 self-start"
            >
              {preview.data.aliases.map((row) => (
                <div key={row.alias} className="contents">
                  <dt className="font-mono text-ink-dim">{row.alias}</dt>
                  <dd className="break-all text-ink">{row.stands_for}</dd>
                </div>
              ))}
            </dl>
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              <pre className="whitespace-pre-wrap break-all rounded-sm border border-line p-3 font-mono text-ink text-xs">
                {`${preview.data.method} ${preview.data.url}\n${preview.data.headers.map((h) => `${h.name}: ${h.value}`).join("\n")}\n\n${preview.data.body}`}
              </pre>
              <CopyButton value={preview.data.body} label="Copy the request body" />
            </div>
          </div>
          <details>
            <summary className="cursor-pointer text-ink">
              Every field and fact kind that can be sent
            </summary>
            <p className="mt-2 break-words font-mono text-ink-dim text-xs">
              {[
                ...preview.data.sendable_fields,
                ...preview.data.fact_kinds.map((kind) => `kind: ${kind}`),
              ].join(", ")}
            </p>
          </details>
        </div>
      )}
    </Section>
  );
}

// ------------------------------------------------------------------- consent

function ConsentSection({
  settings,
  connection,
}: {
  settings: AISettings;
  connection: ConnectionView;
}) {
  const owner = useCan("consent_ai_disclosure");
  const consent = useConsent();
  const [confirming, setConfirming] = useState(false);
  const agreed = connection.consent;
  return (
    <Section id="ai-consent" title="Consent">
      {agreed ? (
        <p className="text-ink">
          {`Agreed by ${agreed.by_name ?? "an owner"} on ${formatDate(agreed.at)}${agreed.current ? "." : ", for values since changed: an owner must agree again."}`}
        </p>
      ) : null}
      <p className="max-w-prose text-ink-dim">{`${whereDataGoes(connection)} ${GATEWAY_LINE}`}</p>
      {owner ? (
        confirming ? (
          <ConfirmPanel label="Agree to send these facts">
            <p className="max-w-prose">
              {`You agree that, while AI wording is on, the facts in the preview above (numbers, times, fixed labels and stand-in names) are sent to ${connection.provider_host} for model ${connection.model}.`}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                pending={consent.isPending}
                onClick={() =>
                  consent.mutate(
                    {
                      fingerprint: settings.consent_fingerprint ?? "",
                      schema_version: settings.schema_version ?? 0,
                    },
                    { onSuccess: () => setConfirming(false) },
                  )
                }
              >
                I agree
              </Button>
              <Button variant="secondary" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          </ConfirmPanel>
        ) : agreed?.current && !agreed.by_left ? null : (
          <div>
            <Button onClick={() => setConfirming(true)}>Agree to send these facts</Button>
          </div>
        )
      ) : agreed?.current ? null : (
        <div className="flex flex-col gap-2">
          <p className="text-ink">
            Waiting for an owner: only an owner can agree. Send them this page.
          </p>
          <CopyButton
            value={`${window.location.origin}/settings/ai`}
            label="Copy this page's link"
          />
        </div>
      )}
      {consent.error ? (
        <InlineFailure
          title={
            isConflict(consent.error)
              ? "The connection changed after you read it"
              : "The agreement was not recorded"
          }
          error={consent.error}
        />
      ) : null}
    </Section>
  );
}

// -------------------------------------------------------------------- switch

function SwitchSection({ settings }: { settings: AISettings }) {
  const change = useSwitch();
  const missing = settings.missing ?? [];
  return (
    <Section id="ai-switch" title="Switch">
      <div className="flex flex-wrap items-center gap-3">
        <Button
          pending={change.isPending}
          disabled={!settings.on && missing.length > 0}
          onClick={() => change.mutate(!settings.on)}
        >
          {settings.on ? "Turn AI wording off" : "Turn AI wording on"}
        </Button>
        {!settings.on && missing.length > 0 ? (
          <p className="text-ink-dim">{`Needs: ${missing.map(missingLine).join("; ")}.`}</p>
        ) : null}
      </div>
      {change.error ? (
        <InlineFailure title="AI wording was not switched" error={change.error} />
      ) : null}
    </Section>
  );
}

// ------------------------------------------------------------------ projects

function ProjectsSection() {
  const projects = useOptOuts(true);
  const change = useSetOptOut();
  return (
    <Section id="ai-projects" title="Projects">
      <p className="max-w-prose text-ink-dim">
        A project kept out keeps its facts and gets no AI wording.
      </p>
      {projects.isPending ? (
        <SkeletonRows rows={2} label="Loading the projects" />
      ) : projects.isError ? (
        <InlineFailure
          title="The projects could not be loaded"
          error={projects.error}
          onRetry={() => void projects.refetch()}
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {projects.data.map((project) => (
            <li key={project.project_id} className="flex flex-wrap items-center gap-3">
              <span className="min-w-0 break-words text-ink">{project.name}</span>
              <span className="text-ink-dim">{project.opted_out ? "Kept out" : "Included"}</span>
              <Button
                variant="secondary"
                pending={change.isPending && change.variables?.projectId === project.project_id}
                onClick={() =>
                  change.mutate({ projectId: project.project_id, optedOut: !project.opted_out })
                }
              >
                {project.opted_out ? "Let it back in" : "Keep it out"}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {change.error ? (
        <InlineFailure title="The project was not changed" error={change.error} />
      ) : null}
    </Section>
  );
}

// --------------------------------------------------------------- usage, runs

function UsageSection() {
  const usage = useUsage(true);
  const runs = useRuns(true);
  const share = usage.data ? usedShare(usage.data) : null;
  return (
    <Section id="ai-usage" title="Usage and runs">
      {usage.isPending ? (
        <SkeletonRows rows={1} label="Loading the usage" />
      ) : usage.isError ? (
        <InlineFailure
          title="The usage could not be loaded"
          error={usage.error}
          onRetry={() => void usage.refetch()}
        />
      ) : (
        <div className="flex flex-col gap-2">
          {share === null ? null : (
            <div
              // The sentence below carries the numbers; the bar only shows them.
              aria-hidden="true"
              className="h-2 w-full overflow-hidden rounded-sm bg-line-subtle"
            >
              <div className="h-full bg-brand" style={{ width: `${share}%` }} />
            </div>
          )}
          <p className="text-ink">{usageLine(usage.data)}</p>
          {costLine(usage.data) ? <p className="text-ink-dim">{costLine(usage.data)}</p> : null}
          <Link className="text-ink underline" to="/settings/explanations">
            Ratings per rule, on Explanations
          </Link>
        </div>
      )}
      {runs.isPending ? (
        <SkeletonRows rows={3} label="Loading the runs" />
      ) : runs.isError ? (
        <InlineFailure
          title="The runs could not be loaded"
          error={runs.error}
          onRetry={() => void runs.refetch()}
        />
      ) : runs.data.length === 0 ? (
        <p className="text-ink-dim">{NO_RUNS}</p>
      ) : (
        <ol aria-label="Runs" className="flex flex-col divide-y divide-line">
          {runs.data.map((run) => (
            <Run key={run.id} run={run} />
          ))}
        </ol>
      )}
    </Section>
  );
}

function Run({ run }: { run: RunRow }) {
  const [open, setOpen] = useState(false);
  const sent = useSent(open ? run.id : null);
  const used =
    run.input_tokens === null ? "—" : tokens((run.input_tokens ?? 0) + (run.output_tokens ?? 0));
  return (
    <li className="flex min-w-0 flex-col gap-1 py-2">
      <div className="grid min-w-0 gap-x-3 gap-y-1 md:grid-cols-[auto_auto_1fr_auto]">
        <span className="text-ink">{formatTimestamp(run.started_at)}</span>
        <span className="text-ink">{`${outcomeLine(run.outcome)}${run.purpose === "check" ? " (test)" : ""}`}</span>
        <span className="min-w-0 break-all text-ink-dim">{run.provider_host}</span>
        <span className="text-ink">{`${used} tokens${run.tokens_estimated ? ", estimated" : ""}`}</span>
      </div>
      <p className="break-words text-ink-dim">
        {[
          run.model ?? "—",
          run.seconds === null ? "—" : `${run.seconds.toFixed(1)} s`,
          run.by_person ? "asked by a person" : "automatic",
          run.fields_sent.length ? `fields: ${run.fields_sent.join(", ")}` : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      </p>
      {run.host_consent === "not_consented" ? (
        <p className="text-warning-text">No owner had agreed to this address when this run went.</p>
      ) : null}
      <div>
        <Button variant="secondary" onClick={() => setOpen(!open)}>
          {open ? "Hide what was sent" : "View what was sent"}
        </Button>
      </div>
      {open ? (
        sent.isPending ? (
          <SkeletonRows rows={2} label="Rebuilding what was sent" />
        ) : sent.isError ? (
          <InlineFailure title="What was sent could not be rebuilt" error={sent.error} />
        ) : (
          <div className="flex min-w-0 flex-col gap-2">
            <p className="font-medium text-ink">{sent.data.statement}</p>
            {(sent.data.body ?? sent.data.facts) ? (
              <pre className="whitespace-pre-wrap break-all rounded-sm border border-line p-3 font-mono text-ink text-xs">
                {sent.data.body ?? sent.data.facts}
              </pre>
            ) : null}
          </div>
        )
      ) : null}
    </li>
  );
}
