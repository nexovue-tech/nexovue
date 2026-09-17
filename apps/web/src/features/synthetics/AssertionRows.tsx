import { EditableRows } from "@/shared/components/EditableRows";

import type { AssertionDraft, ExtractionDraft, FieldProblems } from "./check-draft";
import {
  comparisonsFor,
  comparisonTakesTarget,
  emptyAssertion,
  emptyExtraction,
  rowProblems,
  sourceTakesProperty,
  withAssertionSource,
} from "./check-draft";
import type { AssertionSource, Comparison } from "./check-model";
import { LIMITS } from "./check-model";
import { GroupProblem, InlineInput, InlineSelect } from "./fields";

const SOURCE_LABELS: Record<AssertionSource, string> = {
  status_code: "Status code",
  header: "Header",
  body: "Body text",
  json_path: "JSON path",
  response_time: "Response time (ms)",
  tls_days_remaining: "Certificate days left",
};

export const COMPARISON_LABELS: Record<Comparison, string> = {
  equals: "equals",
  not_equals: "does not equal",
  contains: "contains",
  not_contains: "does not contain",
  less_than: "is less than",
  greater_than: "is greater than",
  matches: "matches pattern",
  is_empty: "is empty",
  is_not_empty: "is not empty",
};

/** What a healthy response looks like, a fact per row — never code (BR5). */
export function AssertionRows({
  rows,
  onChange,
  problems,
  prefix,
}: {
  rows: readonly AssertionDraft[];
  onChange: (rows: AssertionDraft[]) => void;
  problems: FieldProblems;
  prefix: string;
}) {
  return (
    <div className="flex flex-col gap-2">
      <GroupProblem message={problems[`${prefix}.assertions`]} />
      <EditableRows
        label="Assertions"
        rows={rows}
        onChange={onChange}
        newRow={emptyAssertion}
        addLabel="Add assertion"
        emptyLabel="Nothing asserted yet. A check needs at least one, or a failure could never be seen."
        max={LIMITS.maxAssertions}
        rowErrors={rowProblems(problems, `${prefix}.assertions`)}
        renderRow={(row, update, index) => {
          const at = (field: string) => problems[`${prefix}.assertions.${index}.${field}`];
          const comparisons = comparisonsFor(row.source);
          return (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[10rem_minmax(0,1fr)_10rem_minmax(0,1fr)]">
              <InlineSelect
                label={`Assertion ${index + 1} source`}
                value={row.source}
                options={Object.entries(SOURCE_LABELS).map(([value, label]) => ({
                  value: value as AssertionSource,
                  label,
                }))}
                onChange={(source) => update(withAssertionSource(row, source))}
              />
              {sourceTakesProperty(row.source) ? (
                <InlineInput
                  label={`Assertion ${index + 1} ${row.source === "header" ? "header name" : "JSON path"}`}
                  placeholder={row.source === "header" ? "Content-Type" : "$.order.id"}
                  mono
                  value={row.property}
                  onChange={(property) => update({ ...row, property })}
                  error={at("property")}
                />
              ) : (
                <span aria-hidden="true" className="hidden sm:block" />
              )}
              <InlineSelect
                label={`Assertion ${index + 1} comparison`}
                value={row.comparison}
                options={comparisons.map((value) => ({ value, label: COMPARISON_LABELS[value] }))}
                onChange={(comparison) => update({ ...row, comparison })}
              />
              {comparisonTakesTarget(row.comparison) ? (
                <InlineInput
                  label={`Assertion ${index + 1} expected value`}
                  mono
                  value={row.target}
                  onChange={(target) => update({ ...row, target })}
                  error={at("target")}
                />
              ) : (
                <span aria-hidden="true" className="hidden sm:block" />
              )}
            </div>
          );
        }}
      />
    </div>
  );
}

/** Values a later request uses as {{name}} (FR10). */
export function ExtractionRows({
  rows,
  onChange,
  problems,
  prefix,
}: {
  rows: readonly ExtractionDraft[];
  onChange: (rows: ExtractionDraft[]) => void;
  problems: FieldProblems;
  prefix: string;
}) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-ink-dim text-sm">
        Take a value from this response for the requests after it, written as{" "}
        <code className="font-mono">{"{{name}}"}</code>.
      </p>
      <EditableRows
        label="Extracted values"
        rows={rows}
        onChange={onChange}
        newRow={emptyExtraction}
        addLabel="Extract a value"
        emptyLabel="Nothing is passed to the next request."
        max={LIMITS.maxExtractions}
        rowErrors={rowProblems(problems, `${prefix}.extractions`)}
        renderRow={(row, update, index) => {
          const at = (field: string) => problems[`${prefix}.extractions.${index}.${field}`];
          return (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_9rem_minmax(0,2fr)]">
              <InlineInput
                label={`Extraction ${index + 1} name`}
                placeholder="token"
                mono
                value={row.name}
                onChange={(name) => update({ ...row, name })}
                error={at("name")}
              />
              <InlineSelect
                label={`Extraction ${index + 1} from`}
                value={row.source}
                options={[
                  { value: "json_path", label: "JSON path" },
                  { value: "header", label: "Header" },
                  { value: "regex", label: "Pattern" },
                ]}
                onChange={(source) => update({ ...row, source })}
              />
              <InlineInput
                label={`Extraction ${index + 1} expression`}
                placeholder={
                  row.source === "header"
                    ? "X-Request-Id"
                    : row.source === "regex"
                      ? "id=(\\w+)"
                      : "$.token"
                }
                mono
                value={row.expression}
                onChange={(expression) => update({ ...row, expression })}
                error={at("expression")}
              />
            </div>
          );
        }}
      />
    </div>
  );
}
