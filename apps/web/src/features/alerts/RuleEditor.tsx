import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useId, useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { Permitted } from "@/shared/components/Permitted";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { TextField } from "@/shared/components/TextField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import { useCan } from "@/shared/identity";
import { useNothingLive } from "@/shared/refresh";
import { useSelection } from "@/shared/selection";

import type { CheckChoice, RuleDraft, RuleField } from "./alert-words";
import {
  ALERTABLE_STATES,
  CONDITION_NAMES,
  DEFAULT_SEVERITY,
  emptyRuleDraft,
  fieldProblems,
  formatUntil,
  isMuted,
  KIND_NAMES,
  muteUntil,
  ruleDraftOf,
  ruleProblems,
  ruleRequestOf,
  STATE_WORDS,
  sameRule,
  withKind,
} from "./alert-words";
import type { AlertableState, AlertRule, ConditionKind, ServiceReading, Severity } from "./api";
import {
  useAlertRule,
  useChannels,
  useCheckChoices,
  useDeleteRule,
  useMuteRule,
  useSaveRule,
  useServiceReadings,
} from "./api";
import { ChannelForm } from "./ChannelForm";

/**
 * One rule, in the order a person decides it: what to watch, how bad each state is,
 * who to tell, and how often to repeat (design, *Rule editor*). The defaults are
 * already in place, so the shortest path is a name and a channel.
 */

export interface NewRuleSearch {
  /** Prefilled from a check's or a service's page — "Alert me about this" (FR24). */
  check?: string;
  service?: string;
}

export function parseNewRuleSearch(search: Record<string, unknown>): NewRuleSearch {
  if (typeof search.check === "string" && search.check) return { check: search.check };
  if (typeof search.service === "string" && search.service) return { service: search.service };
  return {};
}

const SEVERITIES: readonly { id: Severity; label: string }[] = [
  { id: "critical", label: "Critical" },
  { id: "warning", label: "Warning" },
];

const RENOTIFY: readonly { id: string; label: string }[] = [
  { id: "", label: "Never — once when it opens, once when it resolves" },
  { id: "30", label: "Every 30 minutes while open" },
  { id: "60", label: "Every hour while open" },
  { id: "240", label: "Every 4 hours while open" },
];

export function NewRuleScreen() {
  return (
    <Permitted action="operate_alerts" who="an owner, an admin or a member">
      <NewRuleScreenBody />
    </Permitted>
  );
}

function NewRuleScreenBody() {
  useNothingLive();
  const search = useSearch({ from: "/shell/alerts/rules/new" });
  const { projectId } = useSelection();
  const checks = useCheckChoices(projectId);

  if (projectId === undefined || checks.isPending) {
    return <SkeletonRows rows={6} label="Loading" />;
  }
  const check = checks.data?.find((candidate) => candidate.id === search.check);
  return (
    <RuleEditorPage title="New alert rule">
      <RuleForm
        projectId={projectId}
        initial={emptyRuleDraft({
          checkId: check?.id,
          checkName: check?.name,
          service: search.service,
        })}
      />
    </RuleEditorPage>
  );
}

export function EditRuleScreen() {
  return (
    <Permitted action="operate_alerts" who="an owner, an admin or a member">
      <EditRuleScreenBody />
    </Permitted>
  );
}

function EditRuleScreenBody() {
  useNothingLive();
  const { ruleId } = useParams({ from: "/shell/alerts/rules/$ruleId" });
  const rule = useAlertRule(ruleId);

  return (
    <RuleEditorPage title={rule.data?.name ?? "Alert rule"}>
      {rule.isPending ? (
        <SkeletonRows rows={6} label="Loading the rule" />
      ) : rule.isError ? (
        <InlineFailure
          title="The rule could not be loaded"
          error={rule.error}
          onRetry={() => void rule.refetch()}
        />
      ) : (
        <>
          <RuleControls rule={rule.data} />
          <RuleForm
            projectId={rule.data.project_id}
            stored={rule.data}
            initial={ruleDraftOf(rule.data)}
          />
        </>
      )}
    </RuleEditorPage>
  );
}

function RuleEditorPage({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex h-full min-h-0 flex-col gap-5 overflow-y-auto px-4 pt-5 pb-10">
      <header>
        <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
          <Link to="/alerts" className="hover:text-ink">
            Alerts
          </Link>
        </p>
        <h1 className="mt-1 font-semibold font-serif text-ink text-xl tracking-tight">{title}</h1>
      </header>
      {children}
    </div>
  );
}

function RuleControls({ rule }: { rule: AlertRule }) {
  const mute = useMuteRule();
  const remove = useDeleteRule();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const muted = isMuted(rule.muted_until);

  return (
    <section aria-label="Mute or delete" className="flex max-w-2xl flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {muted ? (
          <>
            <p className="text-sm text-warning-text">
              Muted until {formatUntil(rule.muted_until)}.
            </p>
            <Button
              variant="secondary"
              pending={mute.isPending}
              onClick={() => mute.mutate({ ruleId: rule.id, until: null })}
            >
              Unmute
            </Button>
          </>
        ) : (
          <>
            <span className="text-ink-dim text-sm">Mute for</span>
            {(
              [
                ["1h", "1 hour"],
                ["4h", "4 hours"],
                ["tomorrow", "until 09:00 tomorrow"],
              ] as const
            ).map(([choice, label]) => (
              <Button
                key={choice}
                variant="secondary"
                pending={mute.isPending}
                onClick={() =>
                  mute.mutate({ ruleId: rule.id, until: muteUntil(choice).toISOString() })
                }
              >
                {label}
              </Button>
            ))}
          </>
        )}
        <Button variant="secondary" onClick={() => setConfirming(true)}>
          Delete
        </Button>
      </div>
      {muted ? (
        <p className="text-ink-dim text-sm">
          While muted, incidents are still recorded here and nobody is told — not even when the mute
          ends.
        </p>
      ) : null}
      {confirming ? (
        <div className="border-critical-solid border-l-2 bg-critical-subtle px-3 py-3">
          <p className="font-medium text-ink text-sm">Delete {rule.name}?</p>
          <p className="mt-1 text-ink-dim text-sm leading-relaxed">
            {rule.is_default
              ? "It is this project's default rule. It will not come back by itself."
              : "Nothing it watches will tell anyone any more."}{" "}
            Its open incidents close; their history stays.
          </p>
          <div className="mt-3 flex gap-2">
            <Button
              onClick={() =>
                remove.mutate(rule.id, {
                  onSuccess: () => void navigate({ to: "/alerts", ignoreBlocker: true }),
                })
              }
              pending={remove.isPending}
              pendingLabel="Deleting…"
            >
              Yes, delete {rule.name}
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
      {mute.error ? <InlineFailure title="The mute was not changed" error={mute.error} /> : null}
      {remove.error ? (
        <InlineFailure title="The rule was not deleted" error={remove.error} />
      ) : null}
    </section>
  );
}

function RuleForm({
  projectId,
  stored,
  initial,
}: {
  projectId: string;
  stored?: AlertRule;
  initial: RuleDraft;
}) {
  const save = useSaveRule();
  const channels = useChannels();
  // A member writes rules but channels hold secrets: connecting one is an admin's.
  const canConnect = useCan("manage_channels");
  const navigate = useNavigate();
  const [draft, setDraft] = useState(initial);
  const [baseline, setBaseline] = useState(initial);
  const [attempted, setAttempted] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [saved, setSaved] = useState(false);

  const guard = useUnsavedChanges(!sameRule(draft, baseline));
  const server = fieldProblems<RuleField>(save.error, placeRuleField);
  const problems = { ...server, ...(attempted ? ruleProblems(draft) : {}) };
  const unplaced = save.error && Object.keys(server).length === 0 ? save.error : undefined;

  // Against the current draft, not the one this render saw: a channel created from
  // here answers after the person may have typed more.
  const update = (next: Partial<RuleDraft> | ((current: RuleDraft) => Partial<RuleDraft>)) => {
    setDraft((current) => ({ ...current, ...(typeof next === "function" ? next(current) : next) }));
    setSaved(false);
    if (save.error) save.reset();
  };
  const toggleState = (state: AlertableState) => {
    const states = { ...draft.states };
    if (states[state]) delete states[state];
    else states[state] = DEFAULT_SEVERITY[state];
    update({ states });
  };

  return (
    <>
      <form
        className="flex max-w-2xl flex-col gap-6"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          setAttempted(true);
          if (Object.keys(ruleProblems(draft)).length > 0) return;
          save.mutate(
            { ruleId: stored?.id, rule: ruleRequestOf(draft, projectId) },
            {
              onSuccess: (rule) => {
                setBaseline(draft);
                setSaved(true);
                if (stored === undefined) {
                  // Saved: nothing is unsaved, whatever this render's guard still thinks.
                  void navigate({
                    to: "/alerts/rules/$ruleId",
                    params: { ruleId: rule.id },
                    ignoreBlocker: true,
                  });
                }
              },
            },
          );
        }}
      >
        <Watch
          draft={draft}
          kindFixed={stored !== undefined}
          update={update}
          projectId={projectId}
          problems={problems}
          toggleState={toggleState}
        />

        <fieldset className="flex flex-col gap-3">
          <legend className="font-medium font-serif text-base text-ink">Who to tell</legend>
          {channels.isPending ? (
            <SkeletonRows rows={2} label="Loading channels" />
          ) : channels.isError ? (
            <InlineFailure
              title="Channels could not be loaded"
              error={channels.error}
              onRetry={() => void channels.refetch()}
            />
          ) : (
            channels.data.map((channel) => (
              <label key={channel.id} className="flex items-center gap-2 text-ink text-sm">
                <input
                  type="checkbox"
                  checked={draft.channelIds.includes(channel.id)}
                  onChange={() => update({ channelIds: toggled(draft.channelIds, channel.id) })}
                />
                <span>{channel.name}</span>
                {channel.name === KIND_NAMES[channel.kind] ? null : (
                  <span className="font-mono text-2xs text-ink-dim">
                    {KIND_NAMES[channel.kind]}
                  </span>
                )}
                {channel.health === "failing" ? (
                  <span className="text-critical-text text-xs">failing</span>
                ) : null}
              </label>
            ))
          )}
          {problems.channels ? (
            <p className="text-critical-text text-xs">{problems.channels}</p>
          ) : null}
          {connecting || !canConnect ? null : (
            <div>
              <Button variant="secondary" onClick={() => setConnecting(true)}>
                Connect a new channel
              </Button>
            </div>
          )}
        </fieldset>

        <fieldset className="flex flex-col gap-3">
          <legend className="font-medium font-serif text-base text-ink">Noise</legend>
          <SelectField
            label="Repeat"
            value={draft.renotify}
            options={RENOTIFY}
            optionLabel={(option) => option.label}
            onChange={(renotify) => update({ renotify })}
          />
          <label className="flex items-center gap-2 text-ink text-sm">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={() => update({ enabled: !draft.enabled })}
            />
            <span>On</span>
          </label>
        </fieldset>

        <TextField
          label="Name"
          name="rule-name"
          value={draft.name}
          onChange={(name) => update({ name })}
          error={problems.name}
          hint="Every message names the rule that sent it."
        />

        {unplaced ? <InlineFailure title="The rule was not saved" error={unplaced} /> : null}
        {saved ? (
          <p role="status" className="text-ink text-sm">
            Saved.
          </p>
        ) : null}

        <div className="flex gap-2">
          <Button type="submit" pending={save.isPending} pendingLabel="Saving…">
            {stored === undefined ? "Create rule" : "Save"}
          </Button>
          <Link
            to="/alerts"
            className="rounded-sm border border-line px-3 py-1.5 font-medium text-ink-dim text-sm hover:text-ink"
          >
            Cancel
          </Link>
        </div>
        <UnsavedChangesPrompt guard={guard} what="this rule" />
      </form>

      {connecting ? (
        <section aria-label="Connect a channel" className="max-w-2xl">
          <ChannelForm
            onCancel={() => setConnecting(false)}
            onCreated={(channel) => {
              setConnecting(false);
              update((current) => ({ channelIds: [...current.channelIds, channel.id] }));
            }}
          />
        </section>
      ) : null}
    </>
  );
}

const toggled = (list: string[], id: string) =>
  list.includes(id) ? list.filter((item) => item !== id) : [...list, id];

/** Where the API's field names land on this form. */
function placeRuleField(field: string): RuleField | undefined {
  if (field === "name") return "name";
  if (field === "channel_ids") return "channels";
  if (field.startsWith("condition.check_ids")) return "checks";
  if (field.startsWith("condition.services")) return "services";
  if (field.startsWith("condition.states")) return "states";
  if (field.startsWith("condition.threshold")) return "threshold";
  if (field.startsWith("condition.window_minutes")) return "windowMinutes";
  if (field.startsWith("condition.min_requests")) return "minRequests";
  if (field.includes("_after_minutes")) return "waits";
  if (field.startsWith("condition.silent_minutes")) return "silentMinutes";
  return undefined;
}

const KINDS: readonly { id: ConditionKind; label: string }[] = (
  Object.keys(CONDITION_NAMES) as ConditionKind[]
).map((id) => ({ id, label: CONDITION_NAMES[id] }));

/**
 * What to watch and how bad it is, for every kind of condition. A threshold is set
 * against the last hour's reading of the chosen services, so the number is chosen
 * against real data rather than guessed (design, *Rule editor*).
 */
function Watch({
  draft,
  kindFixed,
  update,
  projectId,
  problems,
  toggleState,
}: {
  draft: RuleDraft;
  /** A saved rule keeps its kind: the API refuses a change, so none is offered. */
  kindFixed: boolean;
  update: (next: Partial<RuleDraft>) => void;
  projectId: string;
  problems: Partial<Record<RuleField, string>>;
  toggleState: (state: AlertableState) => void;
}) {
  const checks = useCheckChoices(projectId);
  const services = useServiceReadings(projectId);
  const threshold = draft.kind === "service_error_rate" || draft.kind === "service_latency_p95";

  return (
    <>
      <fieldset className="flex flex-col gap-3">
        <legend className="font-medium font-serif text-base text-ink">What to watch</legend>
        {kindFixed ? (
          <p className="text-ink text-sm">
            <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">When </span>
            {CONDITION_NAMES[draft.kind]}
            <span className="text-ink-dim"> — to watch something else, create another rule.</span>
          </p>
        ) : (
          <SelectField
            label="When"
            value={draft.kind}
            options={KINDS}
            optionLabel={(option) => option.label}
            onChange={(kind) => update(withKind(draft, kind as ConditionKind))}
          />
        )}
        {draft.kind === "check_state" ? (
          <Scope
            what="check"
            all="Every check in this project, including ones created later"
            scope={draft.scope}
            onScope={(scope) => update({ scope })}
            error={problems.checks}
            choices={checks}
            chosen={draft.checkIds}
            onToggle={(id) => update({ checkIds: toggled(draft.checkIds, id) })}
          />
        ) : (
          <Scope
            what="service"
            all="Every service in this project, including ones that start reporting later"
            scope={draft.scope}
            onScope={(scope) => update({ scope })}
            error={problems.services}
            choices={{
              ...services,
              data: serviceChoices(services.data, draft.services),
            }}
            chosen={draft.services}
            onToggle={(id) => update({ services: toggled(draft.services, id) })}
          />
        )}
      </fieldset>

      <fieldset className="flex flex-col gap-3">
        <legend className="font-medium font-serif text-base text-ink">How bad</legend>
        {draft.kind === "check_state" ? (
          <>
            <p className="text-ink-dim text-sm leading-relaxed">
              A check that goes from one chosen state to a worse one stays one incident and is sent
              again as an escalation. <em>Misconfigured</em> means the check's own settings are
              wrong, not your service — it is off unless you choose it.
            </p>
            {ALERTABLE_STATES.map((state) => (
              <div key={state} className="flex flex-wrap items-center gap-3">
                <label className="flex w-40 items-center gap-2 text-ink text-sm">
                  <input
                    type="checkbox"
                    checked={draft.states[state] !== undefined}
                    onChange={() => toggleState(state)}
                  />
                  <span>When {STATE_WORDS[state]}</span>
                </label>
                {draft.states[state] ? (
                  <SelectField
                    label="Severity"
                    value={draft.states[state] ?? "critical"}
                    options={SEVERITIES}
                    optionLabel={(option) => option.label}
                    onChange={(severity) =>
                      update({ states: { ...draft.states, [state]: severity as Severity } })
                    }
                  />
                ) : null}
              </div>
            ))}
            {problems.states ? (
              <p className="text-critical-text text-xs">{problems.states}</p>
            ) : null}
          </>
        ) : (
          <>
            <SelectField
              label="Severity"
              value={draft.severity}
              options={SEVERITIES}
              optionLabel={(option) => option.label}
              onChange={(severity) => update({ severity: severity as Severity })}
            />
            {threshold ? (
              <>
                <NumberField
                  label={
                    draft.kind === "service_error_rate" ? "At or over, percent" : "At or over, ms"
                  }
                  value={draft.threshold}
                  onChange={(value) => update({ threshold: value })}
                  error={problems.threshold}
                />
                <LastHour draft={draft} readings={services.data} />
                <NumberField
                  label="Over a window of, minutes"
                  value={draft.windowMinutes}
                  onChange={(value) => update({ windowMinutes: value })}
                  error={problems.windowMinutes}
                />
                <NumberField
                  label="Only with at least this many requests in the window"
                  value={draft.minRequests}
                  onChange={(value) => update({ minRequests: value })}
                  error={problems.minRequests}
                  hint="Below it, the rule neither fires nor resolves: five failures in eight requests is not an outage."
                />
                <div className="flex flex-wrap gap-4">
                  <NumberField
                    label="Fire after, minutes"
                    value={draft.fireAfter}
                    onChange={(value) => update({ fireAfter: value })}
                  />
                  <NumberField
                    label="Resolve after, minutes"
                    value={draft.resolveAfter}
                    onChange={(value) => update({ resolveAfter: value })}
                    error={problems.waits}
                  />
                </div>
              </>
            ) : null}
            {draft.kind === "service_silent" ? (
              <NumberField
                label="Silent for, minutes"
                value={draft.silentMinutes}
                onChange={(value) => update({ silentMinutes: value })}
                error={problems.silentMinutes}
                hint="Only a service that sent something in the last day can go silent."
              />
            ) : null}
            {draft.kind === "new_error_group" ? (
              <p className="text-ink-dim text-sm leading-relaxed">
                Each error group is announced once, the first time it happens. Errors this project
                already has are not announced.
              </p>
            ) : null}
          </>
        )}
      </fieldset>
    </>
  );
}

function Scope({
  what,
  all,
  scope,
  onScope,
  error,
  choices,
  chosen,
  onToggle,
}: {
  what: "check" | "service";
  all: string;
  scope: "all" | "chosen";
  onScope: (scope: "all" | "chosen") => void;
  error: string | undefined;
  choices: {
    isPending: boolean;
    isError: boolean;
    error: unknown;
    data: readonly CheckChoice[] | undefined;
    refetch: () => unknown;
  };
  chosen: readonly string[];
  onToggle: (id: string) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <label className="flex items-start gap-2 text-ink text-sm">
        <input
          type="radio"
          name={`rule-scope-${what}`}
          className="mt-1"
          checked={scope === "all"}
          onChange={() => onScope("all")}
        />
        <span>{all}</span>
      </label>
      <label className="flex items-start gap-2 text-ink text-sm">
        <input
          type="radio"
          name={`rule-scope-${what}`}
          className="mt-1"
          checked={scope === "chosen"}
          onChange={() => onScope("chosen")}
        />
        <span>Only the {what}s I choose</span>
      </label>
      {scope === "chosen" ? (
        <div className="ml-6 flex flex-col gap-1.5">
          {choices.isPending ? (
            <SkeletonRows rows={2} label={`Loading this project's ${what}s`} />
          ) : choices.isError ? (
            <InlineFailure
              title={`This project's ${what}s could not be loaded`}
              error={choices.error}
              onRetry={() => void choices.refetch()}
            />
          ) : (choices.data ?? []).length === 0 ? (
            <p className="text-ink-dim text-sm">
              {what === "check"
                ? "This project has no checks yet."
                : "No service has reported in the last hour."}
            </p>
          ) : (
            (choices.data ?? []).map((choice) => (
              <label key={choice.id} className="flex items-center gap-2 text-ink text-sm">
                <input
                  type="checkbox"
                  checked={chosen.includes(choice.id)}
                  onChange={() => onToggle(choice.id)}
                />
                <span>{choice.name}</span>
              </label>
            ))
          )}
        </div>
      ) : null}
      {error ? <p className="text-critical-text text-xs">{error}</p> : null}
    </div>
  );
}

/** The last hour's reading of the chosen services, beside the threshold being set. */
function LastHour({
  draft,
  readings,
}: {
  draft: RuleDraft;
  readings: readonly ServiceReading[] | undefined;
}) {
  if (readings === undefined) return null;
  const shown = readings.filter(
    (reading) => draft.scope === "all" || draft.services.includes(reading.service_name),
  );
  if (shown.length === 0) return null;
  return (
    <div className="text-ink-dim text-sm">
      <p>Last hour:</p>
      <ul className="mt-1 flex flex-col gap-0.5 font-mono text-xs">
        {shown.slice(0, 8).map((reading) => (
          <li key={reading.service_name}>
            {reading.service_name} —{" "}
            {draft.kind === "service_error_rate"
              ? `${(reading.error_rate * 100).toFixed(1)}% errors`
              : `p95 ${Math.round(reading.p95_ms)} ms`}{" "}
            over {reading.request_count.toLocaleString()} requests
          </li>
        ))}
      </ul>
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
  error,
  hint,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  hint?: string;
}) {
  const id = useId();
  const noteId = `${id}-note`;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {label}
      </label>
      <input
        id={id}
        inputMode="decimal"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error || hint ? noteId : undefined}
        className={`w-40 rounded-sm border bg-surface px-2.5 py-1.5 font-mono text-ink text-sm outline-none focus-visible:outline-2 focus-visible:outline-brand ${
          error ? "border-critical-solid" : "border-line hover:border-line-strong"
        }`}
      />
      {error ? (
        <p id={noteId} className="text-critical-text text-xs">
          {error}
        </p>
      ) : hint ? (
        <p id={noteId} className="text-ink-dim text-xs">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/** The services that reported in the last hour, and any already chosen that did not —
 *  a rule for a service that is quiet now must still show which service it is for. */
function serviceChoices(
  readings: readonly ServiceReading[] | undefined,
  chosen: readonly string[],
): CheckChoice[] | undefined {
  if (readings === undefined) return undefined;
  const names = [...new Set([...readings.map((reading) => reading.service_name), ...chosen])];
  return names.sort().map((name) => ({ id: name, name }));
}
