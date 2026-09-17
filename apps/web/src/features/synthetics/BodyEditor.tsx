import { EditableRows } from "@/shared/components/EditableRows";

import type { BodyDraft, FieldProblems, PartDraft, RowDraft } from "./check-draft";
import {
  attachedFileCount,
  defaultContentType,
  emptyPart,
  emptyRow,
  rowProblems,
  withBodyKind,
} from "./check-draft";
import type { BodyKind } from "./check-model";
import { LIMITS } from "./check-model";
import { FileAttach } from "./FileAttach";
import { Checkbox, GroupProblem, InlineInput, InlineSelect } from "./fields";
import { LazyCodeEditor } from "./LazyCodeEditor";

const BODY_KINDS: ReadonlyArray<{ value: BodyKind; label: string }> = [
  { value: "none", label: "none" },
  { value: "text", label: "text" },
  { value: "json", label: "JSON" },
  { value: "xml", label: "XML" },
  { value: "form", label: "form" },
  { value: "multipart", label: "multipart" },
  { value: "binary", label: "binary" },
];

/** A body of any common kind (FR3). One switch, then the editor that kind needs. */
export function BodyEditor({
  body,
  onChange,
  problems,
  prefix,
  projectId,
}: {
  body: BodyDraft;
  onChange: (body: BodyDraft) => void;
  problems: FieldProblems;
  prefix: string;
  projectId: string | undefined;
}) {
  const at = (field: string) => problems[`${prefix}.body.${field}`];
  return (
    <div className="flex flex-col gap-3">
      <fieldset className="flex flex-wrap gap-x-4 gap-y-1">
        <legend className="sr-only">Body type</legend>
        {BODY_KINDS.map((option) => (
          <label key={option.value} className="flex items-center gap-1.5 text-ink text-sm">
            <input
              type="radio"
              name={`${prefix}-body-kind`}
              value={option.value}
              checked={body.kind === option.value}
              onChange={() => onChange(withBodyKind(body, option.value))}
              className="accent-brand"
            />
            {option.label}
          </label>
        ))}
      </fieldset>
      <GroupProblem message={problems[`${prefix}.body`]} />

      {body.kind === "none" ? (
        <p className="text-ink-dim text-sm">This request sends no body.</p>
      ) : null}

      {body.kind === "text" || body.kind === "json" || body.kind === "xml" ? (
        <>
          {body.kind === "text" ? (
            <InlineInput
              label="Content type"
              visibleLabel
              mono
              value={body.contentType}
              placeholder={defaultContentType("text")}
              onChange={(contentType) => onChange({ ...body, contentType })}
              className="max-w-xs"
            />
          ) : null}
          <LazyCodeEditor
            label="Request body"
            language={body.kind === "text" ? "text" : body.kind}
            value={body.text}
            onChange={(text) => onChange({ ...body, text })}
            error={at("content")}
            minLines={6}
          />
          <p className="text-ink-dim text-xs">
            <code className="font-mono">{"{{name}}"}</code> and{" "}
            <code className="font-mono">{"{{secret.NAME}}"}</code> are filled in when the request is
            sent.
          </p>
        </>
      ) : null}

      {body.kind === "form" ? (
        <NameValueRows
          label="Form fields"
          rows={body.fields}
          onChange={(fields) => onChange({ ...body, fields })}
          addLabel="Add field"
          problems={problems}
          prefix={`${prefix}.body.fields`}
        />
      ) : null}

      {body.kind === "multipart" ? (
        <EditableRows
          label="Multipart parts"
          rows={body.parts}
          onChange={(parts) => onChange({ ...body, parts })}
          newRow={() => emptyPart("field")}
          addLabel="Add part"
          emptyLabel="No parts yet."
          max={LIMITS.maxRows}
          rowErrors={rowProblems(problems, `${prefix}.body.parts`)}
          renderRow={(part: PartDraft, update, index) => (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[7rem_minmax(0,1fr)_minmax(0,2fr)]">
              <InlineSelect
                label={`Part ${index + 1} kind`}
                value={part.kind}
                options={[
                  { value: "field", label: "Field" },
                  { value: "file", label: "File" },
                ]}
                onChange={(kind) => update({ ...part, kind })}
              />
              <InlineInput
                label={`Part ${index + 1} name`}
                mono
                value={part.name}
                onChange={(name) => update({ ...part, name })}
                error={at(`parts.${index}.name`)}
              />
              {part.kind === "file" ? (
                <FileAttach
                  label={`Part ${index + 1} file`}
                  projectId={projectId}
                  fileId={part.fileId}
                  fileName={part.fileName}
                  requestIsFull={attachedFileCount(body) >= LIMITS.maxFilesPerRequest}
                  onAttached={(file) => update({ ...part, fileId: file.id, fileName: file.name })}
                  error={at(`parts.${index}.file_id`)}
                />
              ) : (
                <InlineInput
                  label={`Part ${index + 1} value`}
                  mono
                  value={part.value}
                  onChange={(value) => update({ ...part, value })}
                />
              )}
            </div>
          )}
        />
      ) : null}

      {body.kind === "binary" ? (
        <div className="flex flex-col gap-3">
          <InlineInput
            label="Content type"
            visibleLabel
            mono
            value={body.contentType}
            placeholder={defaultContentType("binary")}
            onChange={(contentType) => onChange({ ...body, contentType })}
            className="max-w-xs"
          />
          <FileAttach
            label="File to send"
            projectId={projectId}
            fileId={body.fileId}
            fileName={body.fileName}
            onAttached={(file) => onChange({ ...body, fileId: file.id, fileName: file.name })}
          />
        </div>
      ) : null}
    </div>
  );
}

/** Query parameters, headers and form fields share one shape. */
export function NameValueRows({
  label,
  rows,
  onChange,
  addLabel,
  problems,
  prefix,
  namePlaceholder,
}: {
  label: string;
  rows: readonly RowDraft[];
  onChange: (rows: RowDraft[]) => void;
  addLabel: string;
  problems: FieldProblems;
  prefix: string;
  namePlaceholder?: string;
}) {
  return (
    <EditableRows
      label={label}
      rows={rows}
      onChange={onChange}
      newRow={emptyRow}
      addLabel={addLabel}
      emptyLabel={`No ${label.toLowerCase()}.`}
      max={LIMITS.maxRows}
      rowErrors={rowProblems(problems, prefix)}
      renderRow={(row, update, index) => (
        <div className="grid grid-cols-1 items-start gap-2 sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,2fr)]">
          <div className="pt-1.5">
            <Checkbox
              label={`Send ${label.toLowerCase()} row ${index + 1}`}
              checked={row.enabled}
              onChange={(enabled) => update({ ...row, enabled })}
            />
          </div>
          <InlineInput
            label={`${label} row ${index + 1} name`}
            placeholder={namePlaceholder ?? "name"}
            mono
            value={row.name}
            onChange={(name) => update({ ...row, name })}
            error={problems[`${prefix}.${index}.name`]}
          />
          <InlineInput
            label={`${label} row ${index + 1} value`}
            placeholder="value"
            mono
            value={row.value}
            onChange={(value) => update({ ...row, value })}
            error={problems[`${prefix}.${index}.value`]}
          />
        </div>
      )}
    />
  );
}
