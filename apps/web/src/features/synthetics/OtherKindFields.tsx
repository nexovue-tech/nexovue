import { EditableRows } from "@/shared/components/EditableRows";

import type { CheckDraft, DnsAssertionDraft, FieldProblems } from "./check-draft";
import { newKey, rowProblems } from "./check-draft";
import type { DnsRecordType } from "./check-model";
import { DNS_RECORD_TYPES } from "./check-model";
import { InlineInput, InlineSelect } from "./fields";

/** A port accepts connections (FR17). The host is checked against private addresses. */
export function TcpFields({
  draft,
  onChange,
  problems,
}: {
  draft: CheckDraft;
  onChange: (draft: CheckDraft) => void;
  problems: FieldProblems;
}) {
  return (
    <div className="grid max-w-2xl grid-cols-1 gap-3 sm:grid-cols-[minmax(0,2fr)_7rem_9rem]">
      <InlineInput
        label="Host"
        visibleLabel
        mono
        placeholder="db.example.com"
        value={draft.host}
        onChange={(host) => onChange({ ...draft, host })}
        error={problems["config.host"]}
      />
      <InlineInput
        label="Port"
        visibleLabel
        mono
        inputMode="numeric"
        value={draft.port}
        onChange={(port) => onChange({ ...draft, port })}
        error={problems["config.port"]}
      />
      <InlineInput
        label="Time limit (s)"
        visibleLabel
        inputMode="numeric"
        value={draft.timeoutSeconds}
        onChange={(timeoutSeconds) => onChange({ ...draft, timeoutSeconds })}
        error={problems["config.timeout_seconds"]}
      />
    </div>
  );
}

/** A name resolves, and to what (FR18). */
export function DnsFields({
  draft,
  onChange,
  problems,
}: {
  draft: CheckDraft;
  onChange: (draft: CheckDraft) => void;
  problems: FieldProblems;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid max-w-3xl grid-cols-1 gap-3 sm:grid-cols-[minmax(0,2fr)_6rem_minmax(0,1fr)_8rem]">
        <InlineInput
          label="Domain name"
          visibleLabel
          mono
          placeholder="shop.example.com"
          value={draft.dnsName}
          onChange={(dnsName) => onChange({ ...draft, dnsName })}
          error={problems["config.name"]}
        />
        <InlineSelect
          label="Record"
          visibleLabel
          value={draft.recordType}
          options={DNS_RECORD_TYPES.map((value) => ({ value, label: value }))}
          onChange={(recordType: DnsRecordType) => onChange({ ...draft, recordType })}
        />
        <InlineInput
          label="Resolver (optional)"
          visibleLabel
          mono
          placeholder="1.1.1.1"
          value={draft.resolver}
          onChange={(resolver) => onChange({ ...draft, resolver })}
          error={problems["config.resolver"]}
        />
        <InlineInput
          label="Time limit (s)"
          visibleLabel
          inputMode="numeric"
          value={draft.timeoutSeconds}
          onChange={(timeoutSeconds) => onChange({ ...draft, timeoutSeconds })}
          error={problems["config.timeout_seconds"]}
        />
      </div>
      <div className="flex flex-col gap-2">
        <p className="text-ink-dim text-sm">
          The check is down when the name does not resolve. Assert the answer to catch it resolving
          somewhere else.
        </p>
        <EditableRows
          label="Expected answers"
          rows={draft.dnsAssertions}
          onChange={(dnsAssertions) => onChange({ ...draft, dnsAssertions })}
          newRow={(): DnsAssertionDraft => ({ key: newKey(), comparison: "contains", target: "" })}
          addLabel="Add expected answer"
          emptyLabel="Any answer passes."
          max={10}
          rowErrors={rowProblems(problems, "config.assertions")}
          renderRow={(row, update, index) => (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[12rem_minmax(0,1fr)]">
              <InlineSelect
                label={`Answer ${index + 1} comparison`}
                value={row.comparison}
                options={[
                  { value: "contains", label: "includes" },
                  { value: "not_contains", label: "does not include" },
                  { value: "matches", label: "has one matching" },
                ]}
                onChange={(comparison) => update({ ...row, comparison })}
              />
              <InlineInput
                label={`Answer ${index + 1} value`}
                mono
                placeholder="203.0.113.10"
                value={row.target}
                onChange={(target) => update({ ...row, target })}
                error={problems[`config.assertions.${index}.target`]}
              />
            </div>
          )}
        />
      </div>
    </div>
  );
}
