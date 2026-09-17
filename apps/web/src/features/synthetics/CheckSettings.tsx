import type { CheckDraft, FieldProblems } from "./check-draft";
import { LIMITS } from "./check-model";
import { Checkbox, GroupProblem, InlineInput, InlineSelect } from "./fields";

const INTERVALS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "60", label: "Every minute" },
  { value: "300", label: "Every 5 minutes" },
  { value: "600", label: "Every 10 minutes" },
  { value: "1800", label: "Every 30 minutes" },
  { value: "3600", label: "Every hour" },
  { value: "21600", label: "Every 6 hours" },
  { value: "86400", label: "Every day" },
];

/** What a monitor needs and a request builder does not (design, *Components*). */
export function CheckSettings({
  draft,
  onChange,
  problems,
  availableLocations,
  suggestedName,
}: {
  draft: CheckDraft;
  onChange: (draft: CheckDraft) => void;
  problems: FieldProblems;
  availableLocations: readonly string[];
  suggestedName: string;
}) {
  const heartbeat = draft.kind === "heartbeat";
  const intervalOptions = INTERVALS.some((option) => option.value === draft.intervalSeconds)
    ? INTERVALS
    : [
        ...INTERVALS,
        { value: draft.intervalSeconds, label: `Every ${draft.intervalSeconds} seconds` },
      ];

  return (
    <div className="flex flex-col gap-4">
      <InlineInput
        label="Name"
        visibleLabel
        value={draft.name}
        placeholder={suggestedName || "Checkout API"}
        onChange={(name) => onChange({ ...draft, name })}
        error={problems.name}
        hint={
          suggestedName && !draft.name ? `Left empty, it is called “${suggestedName}”.` : undefined
        }
        className="max-w-md"
      />

      <div className="grid max-w-2xl grid-cols-1 gap-3 sm:grid-cols-2">
        <InlineSelect
          label={heartbeat ? "Expect a call" : "Run"}
          visibleLabel
          value={draft.intervalSeconds}
          options={intervalOptions}
          onChange={(intervalSeconds) => onChange({ ...draft, intervalSeconds })}
          error={problems.interval_seconds}
        />
        {heartbeat ? (
          <InlineInput
            label="Grace (seconds)"
            visibleLabel
            inputMode="numeric"
            value={draft.graceSeconds}
            onChange={(graceSeconds) => onChange({ ...draft, graceSeconds })}
            error={problems["config.grace_seconds"]}
            hint="How late a call may be before the job counts as missed."
          />
        ) : (
          <InlineSelect
            label="Retries before a failure counts"
            visibleLabel
            value={draft.retries}
            options={Array.from({ length: LIMITS.maxRetries }, (_, index) => ({
              value: String(index + 1),
              label: String(index + 1),
            }))}
            onChange={(retries) => onChange({ ...draft, retries })}
            error={problems.retries}
          />
        )}
      </div>

      {heartbeat ? null : (
        <>
          <fieldset className="flex flex-col gap-2">
            <legend className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
              Locations
            </legend>
            <p className="text-ink-dim text-xs">
              Each location runs the check on its own. A failure is retried from the next location
              before it counts; with one location, from the same one.
            </p>
            <Checkbox
              label="Every location"
              checked={draft.locations.length === 0}
              onChange={(every) =>
                onChange({ ...draft, locations: every ? [] : [...availableLocations] })
              }
            />
            {draft.locations.length > 0
              ? availableLocations.map((location) => (
                  <Checkbox
                    key={location}
                    label={location}
                    checked={draft.locations.includes(location)}
                    onChange={(checked) =>
                      onChange({
                        ...draft,
                        locations: checked
                          ? [...draft.locations, location]
                          : draft.locations.filter((chosen) => chosen !== location),
                      })
                    }
                  />
                ))
              : null}
            <GroupProblem message={problems.locations} />
          </fieldset>

          <div className="grid max-w-2xl grid-cols-1 gap-3 sm:grid-cols-2">
            <InlineInput
              label="Degraded above (ms)"
              visibleLabel
              inputMode="numeric"
              value={draft.degradedMs}
              onChange={(degradedMs) => onChange({ ...draft, degradedMs })}
              error={problems.degraded_ms}
              hint="Passing, but slower than this, is degraded. Empty: never."
            />
            <InlineInput
              label="Failing above (ms)"
              visibleLabel
              inputMode="numeric"
              value={draft.failingMs}
              onChange={(failingMs) => onChange({ ...draft, failingMs })}
              error={problems.failing_ms}
              hint="Slower than this is down, whatever the response. Empty: never."
            />
          </div>
        </>
      )}
    </div>
  );
}
