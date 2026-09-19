import type { FlameNode } from "@/shared/components/FlameGraph";
import { FlameGraph } from "@/shared/components/FlameGraph";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import type { TimeWindow } from "@/shared/selection";
import { previousWindow } from "@/shared/selection";
import type { ComparisonSide, DiffNode, FilterValue, FunctionChange } from "./api";
import { useProfileComparison } from "./api";
import { apiValue, NOT_STATED } from "./filters";
import { changeReadingOf } from "./profile-reading";
import { formatValue, typeName } from "./profile-words";

/** How the two sides differ: by version over one window, or by window for all versions. */
export type CompareBy = "versions" | "ranges";

/** Which earlier window a range comparison is measured against. */
export type BaselineRange = "previous" | "day" | "week";

const BASELINE_RANGES: ReadonlyArray<{ id: BaselineRange; label: string }> = [
  { id: "previous", label: "The window before this one" },
  { id: "day", label: "The same window a day earlier" },
  { id: "week", label: "The same window a week earlier" },
];

const MS_PER_DAY = 86_400_000;

/** A baseline range as it reads inside a sentence: "the window before this one". */
function rangeName(baseline: BaselineRange): string {
  const label = BASELINE_RANGES.find((range) => range.id === baseline)?.label ?? "the baseline";
  return label.charAt(0).toLowerCase() + label.slice(1);
}

/** How many functions each of the two change tables lists. */
const LISTED_CHANGES = 25;

export interface CompareChoice {
  by: CompareBy;
  /** Versions: the baseline and the comparison. Unset, the two latest are used. */
  baseVersion?: string;
  version?: string;
  /** Ranges: which earlier window the current one is compared with. */
  baseline: BaselineRange;
}

/**
 * Two profiles of one service and type, compared by share (FR9): which functions
 * take more of the whole than they did, and which take less. Shares, not values,
 * so a busier or longer comparison side does not read as everything growing.
 */
export function CompareView({
  projectId,
  selection,
  timeWindow,
  versions,
  choice,
  onChoice,
  phrase,
}: {
  projectId: string | undefined;
  /** `version` narrows both sides of a range comparison; versions set their own. */
  selection: {
    service: string;
    sampleType: string;
    host?: string;
    version?: string;
    environment?: string;
  };
  timeWindow: TimeWindow;
  /** The versions the selection's samples carry in the window, once they have loaded. */
  versions: {
    data: readonly FilterValue[] | undefined;
    isError: boolean;
    error: unknown;
    refetch: () => void;
  };
  choice: CompareChoice;
  onChoice: (next: Partial<CompareChoice>) => void;
  phrase: string;
}) {
  const latestFirst = [...(versions.data ?? [])].sort((a, b) =>
    b.last_seen.localeCompare(a.last_seen),
  );
  const shown = (value: string) => (value === "" ? NOT_STATED : value);
  const comparisonVersion =
    choice.version ?? (latestFirst[0] ? shown(latestFirst[0].value) : undefined);
  const baseVersion =
    choice.baseVersion ?? (latestFirst[1] ? shown(latestFirst[1].value) : undefined);

  const sameVersion =
    choice.by === "versions" && baseVersion !== undefined && baseVersion === comparisonVersion;
  // Undecided while the versions load: "no versions" is only said of an answer.
  const versionsKnown = versions.data !== undefined;
  const tooFewVersions = choice.by === "versions" && versionsKnown && latestFirst.length < 2;

  const sides = sidesOf(choice, timeWindow, baseVersion, comparisonVersion, selection.version);
  const ready = !sameVersion && !tooFewVersions && (choice.by === "ranges" || versionsKnown);
  const compared = useProfileComparison(
    projectId,
    ready
      ? {
          service: selection.service,
          sampleType: selection.sampleType,
          host: selection.host,
          environment: selection.environment,
        }
      : undefined,
    ready ? sides.baseline : undefined,
    ready ? sides.comparison : undefined,
  );

  const versionOptions = latestFirst.map((version) => ({ id: shown(version.value) }));

  return (
    <section aria-labelledby="profile-compare" className="flex flex-col gap-4">
      <h2 id="profile-compare" className="font-medium font-serif text-base text-ink">
        Compare {typeName(selection.sampleType)}
      </h2>
      <div className="flex flex-wrap items-baseline gap-4">
        <SelectField
          label="Compare"
          value={choice.by}
          options={[{ id: "versions" }, { id: "ranges" }]}
          optionLabel={(option) => (option.id === "versions" ? "Two versions" : "Two time ranges")}
          onChange={(by) => onChoice({ by: by as CompareBy })}
        />
        {choice.by === "versions" && !tooFewVersions ? (
          <>
            <SelectField
              label="Baseline"
              value={baseVersion ?? ""}
              options={versionOptions}
              optionLabel={(option) => option.id}
              onChange={(value) => onChoice({ baseVersion: value })}
            />
            <SelectField
              label="Against"
              value={comparisonVersion ?? ""}
              options={versionOptions}
              optionLabel={(option) => option.id}
              onChange={(value) => onChoice({ version: value })}
            />
            {sameVersion ? (
              <p role="alert" className="text-critical-text text-xs">
                Pick two different versions — a version compared with itself shows no change.
              </p>
            ) : null}
          </>
        ) : null}
        {choice.by === "ranges" ? (
          <SelectField
            label="Against"
            value={choice.baseline}
            options={BASELINE_RANGES}
            optionLabel={(option) => option.label}
            onChange={(value) => onChoice({ baseline: value as BaselineRange })}
          />
        ) : null}
      </div>

      {choice.by === "versions" && versions.isError ? (
        <InlineFailure
          title="The versions could not be loaded"
          error={versions.error}
          onRetry={versions.refetch}
        />
      ) : choice.by === "versions" && !versionsKnown ? (
        <SkeletonRows rows={3} label="Loading the versions" />
      ) : tooFewVersions ? (
        <p className="text-ink-dim text-sm">
          {latestFirst.length === 0
            ? `No sample ${phrase} names a version.`
            : `Only one version (${shown(latestFirst[0].value)}) sent profiles ${phrase}.`}{" "}
          <button
            type="button"
            onClick={() => onChoice({ by: "ranges" })}
            className="text-brand-text hover:underline"
          >
            Compare two time ranges instead
          </button>
        </p>
      ) : !ready ? null : compared.isPending ? (
        <SkeletonRows rows={6} label="Comparing the two profiles" />
      ) : compared.isError ? (
        <InlineFailure
          title="The comparison could not be loaded"
          error={compared.error}
          onRetry={() => void compared.refetch()}
        />
      ) : compared.data.baseline_samples === 0 || compared.data.comparison_samples === 0 ? (
        <p role="status" className="text-ink-dim text-sm">
          {compared.data.baseline_samples === 0
            ? "The baseline has no profiles, so there is nothing to compare against."
            : "The comparison side has no profiles, so there is nothing to compare."}
        </p>
      ) : (
        <>
          <p role="status" className="font-medium font-serif text-base text-ink">
            {changeReadingOf(
              choice.by === "versions"
                ? `From version ${baseVersion} to ${comparisonVersion}`
                : `Compared with ${rangeName(choice.baseline)}`,
              compared.data.functions,
            )}
          </p>
          <FlameGraph
            root={asFlameNode(compared.data.root)}
            formatValue={(value) => formatValue(value, compared.data.unit)}
            mergedFrames={compared.data.merged_frames}
            difference
          />
          <div className="grid gap-6 lg:grid-cols-2">
            <ChangesTable
              title="Grew most"
              functions={compared.data.functions
                .filter((row) => row.change > 0)
                .slice(0, LISTED_CHANGES)}
              empty="No function's share grew."
            />
            <ChangesTable
              title="Shrank most"
              functions={compared.data.functions
                .filter((row) => row.change < 0)
                .reverse()
                .slice(0, LISTED_CHANGES)}
              empty="No function's share shrank."
            />
          </div>
        </>
      )}
    </section>
  );
}

function sidesOf(
  choice: CompareChoice,
  timeWindow: TimeWindow,
  baseVersion: string | undefined,
  comparisonVersion: string | undefined,
  filteredVersion: string | undefined,
): { baseline: ComparisonSide; comparison: ComparisonSide } {
  const current = { from: timeWindow.from, to: timeWindow.to };
  if (choice.by === "versions") {
    return {
      baseline: { ...current, version: apiValue(baseVersion) },
      comparison: { ...current, version: apiValue(comparisonVersion) },
    };
  }
  return {
    baseline: { ...earlier(timeWindow, choice.baseline), version: filteredVersion },
    comparison: { ...current, version: filteredVersion },
  };
}

function earlier(timeWindow: TimeWindow, baseline: BaselineRange): TimeWindow {
  if (baseline === "previous") return previousWindow(timeWindow);
  const shift = baseline === "day" ? MS_PER_DAY : 7 * MS_PER_DAY;
  return {
    from: new Date(Date.parse(timeWindow.from) - shift).toISOString(),
    to: new Date(Date.parse(timeWindow.to) - shift).toISOString(),
  };
}

/** The comparison tree in the flame graph's own shape: width is the averaged share. */
function asFlameNode(node: DiffNode): FlameNode {
  return {
    name: node.name,
    file: node.file,
    unsymbolized: node.unsymbolized,
    total: node.weight,
    self_value: 0,
    merged: node.merged,
    baseline_share: node.baseline_share,
    comparison_share: node.comparison_share,
    children: node.children.map(asFlameNode),
  };
}

function ChangesTable({
  title,
  functions,
  empty,
}: {
  title: string;
  functions: readonly FunctionChange[];
  empty: string;
}) {
  if (functions.length === 0) {
    return (
      <section className="flex flex-col gap-1">
        <h3 className="font-medium text-ink text-sm">{title}</h3>
        <p className="text-ink-dim text-sm">{empty}</p>
      </section>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="pb-1 text-left font-medium text-ink text-sm">
          {title} — by the change in the function's own share of the whole
        </caption>
        <thead>
          <tr className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
            <th scope="col" className="border-line border-b pr-4 pb-1.5 text-left font-normal">
              Function
            </th>
            <th scope="col" className="border-line border-b pb-1.5 pl-4 text-right font-normal">
              Baseline
            </th>
            <th scope="col" className="border-line border-b pb-1.5 pl-4 text-right font-normal">
              Now
            </th>
            <th scope="col" className="border-line border-b pb-1.5 pl-4 text-right font-normal">
              Change
            </th>
          </tr>
        </thead>
        <tbody>
          {functions.map((row) => (
            <tr
              key={`${row.name}|${row.file}|${row.unsymbolized}`}
              className="border-line-subtle border-b"
            >
              <td
                className={`max-w-0 truncate py-1.5 pr-4 ${row.unsymbolized ? "text-ink-dim italic" : "text-ink"}`}
              >
                {row.unsymbolized ? `${row.name} (unsymbolized)` : row.name}
              </td>
              <td className="py-1.5 pl-4 text-right text-ink-dim tabular-nums">
                {percent(row.baseline_self)}
              </td>
              <td className="py-1.5 pl-4 text-right text-ink tabular-nums">
                {percent(row.comparison_self)}
              </td>
              <td
                data-change={row.change > 0 ? "more" : row.change < 0 ? "less" : "same"}
                className={`py-1.5 pl-4 text-right tabular-nums ${
                  row.change > 0
                    ? "text-critical-text"
                    : row.change < 0
                      ? "text-info-text"
                      : "text-ink-dim"
                }`}
              >
                {points(row.change)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function percent(share: number): string {
  const value = share * 100;
  return value < 10 ? `${value.toFixed(1)} %` : `${Math.round(value)} %`;
}

/** A change in share, in percentage points, signed. */
function points(change: number): string {
  const value = change * 100;
  if (Math.abs(value) < 0.05) return "±0.0 pts";
  return `${value > 0 ? "+" : "−"}${Math.abs(value).toFixed(1)} pts`;
}
