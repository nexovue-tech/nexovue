/**
 * One row of filter controls, declared rather than hand-built.
 *
 * Nine screens need to narrow a list and none of them needs its own row of
 * buttons to do it. A screen declares what it can be asked — a name, a label
 * and how the value is chosen — and this renders the controls, the chips that
 * say what is currently applied, and the way out of each one.
 *
 * **Values are strings, and absent means "not filtering".** The URL is where a
 * filter lives, and the URL holds strings, so a screen converts once at its own
 * edge instead of this component learning every screen's types. A key missing
 * from `values` is not applied; `""` never reaches the caller.
 */

import { X } from "lucide-react";

export interface FilterOption {
  value: string;
  label: string;
}

/**
 * How one filter is chosen.
 *
 * `toggle` writes `on` when checked and clears the key when not, because "only
 * failing" is a question with one interesting answer — a tri-state select
 * offering "all / failing / not failing" reads as three choices where there are
 * two.
 */
export type FilterControl =
  | { kind: "select"; options: readonly FilterOption[] }
  | { kind: "toggle"; on: string };

export interface FilterDefinition {
  key: string;
  label: string;
  control: FilterControl;
  /** How the applied value reads in its chip. Defaults to the option's label. */
  describe?: (value: string) => string;
}

export type FilterValues = Readonly<Record<string, string | undefined>>;

export interface AppliedFilter {
  key: string;
  label: string;
  value: string;
}

/**
 * What is currently narrowing the list, in the order the screen declared.
 *
 * Exported because a screen needs the same answer to write its empty state —
 * "none match these filters" is a different sentence from "nothing arrived",
 * and only the caller knows which one it is showing.
 */
export function appliedFilters(
  definitions: readonly FilterDefinition[],
  values: FilterValues,
): AppliedFilter[] {
  const applied: AppliedFilter[] = [];
  for (const definition of definitions) {
    const value = values[definition.key];
    if (value === undefined || value === "") continue;
    applied.push({
      key: definition.key,
      label: definition.label,
      value: describe(definition, value),
    });
  }
  return applied;
}

function describe(definition: FilterDefinition, value: string): string {
  if (definition.describe) return definition.describe(value);
  if (definition.control.kind === "toggle") return "yes";
  return definition.control.options.find((option) => option.value === value)?.label ?? value;
}

export function FilterBar({
  definitions,
  values,
  onChange,
  onClearAll,
}: {
  definitions: readonly FilterDefinition[];
  values: FilterValues;
  onChange: (key: string, value: string | undefined) => void;
  onClearAll: () => void;
}) {
  const applied = appliedFilters(definitions, values);

  return (
    <div className="shrink-0 px-4 pb-3">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2">
        {definitions.map((definition) =>
          definition.control.kind === "toggle" ? (
            <FilterToggle
              key={definition.key}
              definition={definition}
              on={definition.control.on}
              checked={values[definition.key] !== undefined && values[definition.key] !== ""}
              onChange={onChange}
            />
          ) : (
            <FilterSelect
              key={definition.key}
              definition={definition}
              options={definition.control.options}
              value={values[definition.key] ?? ""}
              onChange={onChange}
            />
          ),
        )}
      </div>

      {applied.length > 0 ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {applied.map((filter) => (
            <FilterChip
              key={filter.key}
              filter={filter}
              onClear={() => onChange(filter.key, undefined)}
            />
          ))}
          <button
            type="button"
            onClick={onClearAll}
            className="rounded-sm px-1.5 py-0.5 font-mono text-2xs text-ink-dim uppercase tracking-caps hover:bg-hover hover:text-ink focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
          >
            Clear all
          </button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Native selects, deliberately: keyboard navigation, typeahead and the mobile
 * picker all keep working, and there is nothing here a listbox would add.
 */
function FilterSelect({
  definition,
  options,
  value,
  onChange,
}: {
  definition: FilterDefinition;
  options: readonly FilterOption[];
  value: string;
  onChange: (key: string, value: string | undefined) => void;
}) {
  return (
    <label className="flex items-baseline gap-2">
      <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {definition.label}
      </span>
      <select
        value={value}
        onChange={(event) => onChange(definition.key, event.target.value || undefined)}
        className="max-w-44 cursor-pointer appearance-none truncate rounded-sm border-none bg-transparent py-0.5 pr-1 pl-1 font-medium text-ink text-sm outline-none hover:bg-hover focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
      >
        {withSelected(options, value).map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function FilterToggle({
  definition,
  on,
  checked,
  onChange,
}: {
  definition: FilterDefinition;
  on: string;
  checked: boolean;
  onChange: (key: string, value: string | undefined) => void;
}) {
  return (
    <label className="flex cursor-pointer items-baseline gap-2">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(definition.key, event.target.checked ? on : undefined)}
        className="cursor-pointer accent-brand focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
      />
      <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {definition.label}
      </span>
    </label>
  );
}

/** What is currently narrowing the list, and the way out of each one. */
function FilterChip({ filter, onClear }: { filter: AppliedFilter; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-sm border border-line bg-raised py-0.5 pr-1 pl-2 text-2xs">
      <span className="text-ink-dim">{filter.label}</span>
      <span className="font-mono text-ink">{filter.value}</span>
      <button
        type="button"
        onClick={onClear}
        aria-label={`Remove the ${filter.label.toLowerCase()} filter`}
        className="rounded-sm p-0.5 text-ink-dim hover:bg-hover hover:text-ink focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
      >
        <X size={11} aria-hidden="true" />
      </button>
    </span>
  );
}

/**
 * The declared options, plus whatever is already selected.
 *
 * A shared link can name a host that has since gone quiet. Without it in the
 * list a native select silently shows "All" while the filter is still applied —
 * the control saying one thing and the results another.
 */
function withSelected(options: readonly FilterOption[], selected: string): readonly FilterOption[] {
  const known = options.some((option) => option.value === selected);
  const named =
    selected === "" || known ? options : [{ value: selected, label: selected }, ...options];
  return named[0]?.value === "" ? named : [{ value: "", label: "All" }, ...named];
}
