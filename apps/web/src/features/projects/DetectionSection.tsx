import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import { UnsavedChangesPrompt, useUnsavedChanges } from "@/shared/components/UnsavedChanges";
import type { DetectionSettings, FindingStats, Sensitivity } from "@/shared/detection/api";
import {
  useDetectionSettings,
  useFindingStats,
  useSaveDetectionSettings,
} from "@/shared/detection/api";
import { formatCount } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";

/**
 * A project's anomaly detection (F6): how sensitive it is (FR18), whose clock its
 * normal follows (FR24), and how often it has been wrong (FR26). Shown only to who may
 * change it, as every gated section here is (C10).
 *
 * The form seeds from the settings once they are read and never re-syncs: the caller
 * mounts it per project, so a switched project cannot leave the last one's values in
 * the fields.
 */
export function DetectionSection({ projectId }: { projectId: string }) {
  const settings = useDetectionSettings(projectId);
  const stats = useFindingStats(projectId);

  return (
    <section className="flex flex-col gap-4">
      <div className="max-w-2xl">
        <h3 className="font-medium font-serif text-base text-ink tracking-tight">Detection</h3>
        <p className="mt-1 text-ink-dim text-sm leading-relaxed">
          How far a service must move from its own normal before it is a finding, and the clock its
          days and weeks follow.
        </p>
      </div>
      {settings.isPending ? null : settings.isError ? (
        <InlineFailure
          title="The detection settings could not be loaded"
          error={settings.error}
          onRetry={() => void settings.refetch()}
        />
      ) : (
        <DetectionForm projectId={projectId} stored={settings.data} />
      )}
      {stats.data ? (
        <p className="max-w-prose text-ink-dim text-sm">{describeStats(stats.data)}</p>
      ) : null}
    </section>
  );
}

interface SensitivityChoice {
  id: Sensitivity;
  label: string;
}

/** Spec, *Product values*: each level's sustain time and minimum change, in words. */
const SENSITIVITIES: readonly SensitivityChoice[] = [
  { id: "low", label: "Low — 10 minutes, twice as slow, +5 points failing" },
  { id: "normal", label: "Normal — 5 minutes, 1.5 times as slow, +2 points failing" },
  { id: "high", label: "High — 3 minutes, 1.25 times as slow, +1 point failing" },
];

/** Every zone the browser knows, so the choice is one the API will accept. */
function zoneChoices(current: string): { id: string }[] {
  const known = new Set(Intl.supportedValuesOf("timeZone"));
  known.add("UTC");
  known.add(current);
  return [...known].sort().map((zone) => ({ id: zone }));
}

function DetectionForm({ projectId, stored }: { projectId: string; stored: DetectionSettings }) {
  const save = useSaveDetectionSettings(projectId);
  const [sensitivity, setSensitivity] = useState<Sensitivity>(stored.sensitivity);
  const [timezone, setTimezone] = useState(stored.timezone);
  const [saved, setSaved] = useState(false);
  const [zones] = useState(() => zoneChoices(stored.timezone));

  const changed = sensitivity !== stored.sensitivity || timezone !== stored.timezone;
  const guard = useUnsavedChanges(changed);

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        setSaved(false);
        save.mutate({ sensitivity, timezone }, { onSuccess: () => setSaved(true) });
      }}
    >
      <UnsavedChangesPrompt guard={guard} what="the detection settings" />
      <div className="flex flex-wrap gap-4">
        <div className="w-full max-w-md">
          <SelectField
            label="Sensitivity"
            value={sensitivity}
            options={SENSITIVITIES}
            onChange={(id) => {
              setSaved(false);
              setSensitivity(id as Sensitivity);
            }}
            optionLabel={(choice) => choice.label}
            stacked
          />
        </div>
        <div className="w-full max-w-xs">
          <SelectField
            label="Time zone"
            value={timezone}
            options={zones}
            onChange={(id) => {
              setSaved(false);
              setTimezone(id);
            }}
            optionLabel={(zone) => zone.id}
            stacked
          />
        </div>
      </div>
      {timezone !== stored.timezone ? (
        <p className="max-w-prose text-warning-text text-sm">
          Each minute is compared with the same local time on earlier days, so the normal moves to
          the new clock. Nothing starts learning again: the history is the same.
        </p>
      ) : null}
      <div>
        <Button type="submit" pending={save.isPending} pendingLabel="Saving" disabled={!changed}>
          Save detection
        </Button>
      </div>
      {saved && !changed ? (
        <p role="status" className="text-ink text-sm">
          Saved. It applies from the next judged minute.
        </p>
      ) : null}
      {save.error ? (
        <InlineFailure title="The detection settings were not saved" error={save.error} />
      ) : null}
    </form>
  );
}

/** FR26, against the budget of one false finding per service per week (spec). */
export function describeStats(stats: FindingStats): string {
  const window = `In the last ${formatCount(stats.days)} ${plural(stats.days, "day")}`;
  const opened = `${formatCount(stats.opened)} ${plural(stats.opened, "finding")} opened`;
  const marked = `${formatCount(stats.false_alarms)} ${stats.false_alarms === 1 ? "was" : "were"} marked a false alarm`;
  if (stats.false_finding_rate_per_service_week === null) {
    return `${window}, ${opened}. No service has been judged yet, so there is no false-alarm rate.`;
  }
  const rate = trimmed(stats.false_finding_rate_per_service_week);
  return `${window}, ${opened} and ${marked}: ${rate} false findings per service per week, against a budget of 1.`;
}

const RATE_DIGITS = 2;

function trimmed(value: number): string {
  return value.toFixed(RATE_DIGITS).replace(/\.?0+$/, "");
}
