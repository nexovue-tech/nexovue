import { useState } from "react";

import { Button } from "@/shared/components/Button";
import type { TabItem } from "@/shared/components/Tabs";
import { Tabs } from "@/shared/components/Tabs";

import { AssertionRows, ExtractionRows } from "./AssertionRows";
import { AuthHelper } from "./AuthHelper";
import { BodyEditor, NameValueRows } from "./BodyEditor";
import type { FieldProblems, RequestDraft } from "./check-draft";
import { COMMON_METHODS, problemsUnder } from "./check-draft";
import { LIMITS } from "./check-model";
import { Checkbox, FieldError, InlineInput } from "./fields";

type RequestTab = "params" | "auth" | "headers" | "body" | "assertions" | "extract" | "request";

const CUSTOM_METHOD = "__custom__";

/**
 * One request, laid out the way Postman lays one out, because developers already read
 * that layout without thinking (design, *The request editor*). Where it differs, it
 * differs on purpose: Run now stands where Send does and saves nothing, and Assertions
 * replaces Tests — rows, never code.
 */
export function RequestEditor({
  request,
  onChange,
  problems,
  prefix,
  projectId,
  secretNames,
  allowExtractions,
  onRunNow,
  runPending,
}: {
  request: RequestDraft;
  onChange: (request: RequestDraft) => void;
  problems: FieldProblems;
  prefix: string;
  projectId: string | undefined;
  secretNames: readonly string[];
  allowExtractions: boolean;
  onRunNow?: () => void;
  runPending?: boolean;
}) {
  const [tab, setTab] = useState<RequestTab>("params");
  // Its own flag: typing "GET" into Other… must not swap the input for the list mid-word.
  const [choseOther, setChoseOther] = useState(false);
  const customMethod =
    choseOther || !(COMMON_METHODS as readonly string[]).includes(request.method);
  const under = (field: string) => problemsUnder(problems, `${prefix}.${field}`).length > 0;
  const enabledCount = (rows: ReadonlyArray<{ name: string; enabled: boolean }>) =>
    rows.filter((row) => row.enabled && row.name).length;

  const tabs: TabItem[] = [
    { id: "params", label: "Params", count: enabledCount(request.query), invalid: under("query") },
    { id: "auth", label: "Authorization", invalid: under("auth") },
    {
      id: "headers",
      label: "Headers",
      count: enabledCount(request.headers),
      invalid: under("headers"),
    },
    { id: "body", label: "Body", invalid: under("body") },
    {
      id: "assertions",
      label: "Assertions",
      count: request.assertions.length,
      invalid: under("assertions"),
    },
    ...(allowExtractions
      ? [
          {
            id: "extract",
            label: "Extract",
            count: request.extractions.length,
            invalid: under("extractions"),
          },
        ]
      : []),
    { id: "request", label: "Settings", invalid: under("timeout_seconds") || under("name") },
  ];

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <div className="flex gap-2 sm:w-44 sm:shrink-0">
          <label className="sr-only" htmlFor={`${request.key}-method`}>
            Method
          </label>
          <select
            id={`${request.key}-method`}
            value={customMethod ? CUSTOM_METHOD : request.method}
            onChange={(event) => {
              const other = event.target.value === CUSTOM_METHOD;
              setChoseOther(other);
              onChange({ ...request, method: other ? "" : event.target.value });
            }}
            className="w-full rounded-sm border border-line bg-surface px-2 py-1 font-medium font-mono text-ink text-sm outline-none hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand"
          >
            {COMMON_METHODS.map((method) => (
              <option key={method} value={method}>
                {method}
              </option>
            ))}
            <option value={CUSTOM_METHOD}>Other…</option>
          </select>
          {customMethod ? (
            <InlineInput
              label="Method"
              placeholder="PROPFIND"
              mono
              value={request.method}
              // RFC 9110 methods are case-sensitive, so what is typed is what is sent.
              onChange={(method) => onChange({ ...request, method })}
              error={problems[`${prefix}.method`]}
            />
          ) : null}
        </div>
        <InlineInput
          label="URL"
          placeholder="https://api.example.com/orders/{{order_id}}"
          mono
          type="url"
          inputMode="url"
          value={request.url}
          onChange={(url) => onChange({ ...request, url })}
          error={problems[`${prefix}.url`]}
          className="flex-1"
        />
        {onRunNow ? (
          <div className="shrink-0">
            <Button
              variant="secondary"
              onClick={onRunNow}
              pending={runPending}
              pendingLabel="Running"
            >
              Run now
            </Button>
          </div>
        ) : null}
      </div>
      {problems[prefix] ? (
        <FieldError id={`${request.key}-problem`} message={problems[prefix]} />
      ) : null}

      <Tabs label="Request" items={tabs} selected={tab} onSelect={(id) => setTab(id as RequestTab)}>
        <div className="pt-3">
          {tab === "params" ? (
            <NameValueRows
              label="Query parameters"
              rows={request.query}
              onChange={(query) => onChange({ ...request, query })}
              addLabel="Add parameter"
              problems={problems}
              prefix={`${prefix}.query`}
            />
          ) : null}
          {tab === "auth" ? (
            <AuthHelper
              auth={request.auth}
              onChange={(auth) => onChange({ ...request, auth })}
              problems={problems}
              prefix={prefix}
              secretNames={secretNames}
            />
          ) : null}
          {tab === "headers" ? (
            <NameValueRows
              label="Headers"
              rows={request.headers}
              onChange={(headers) => onChange({ ...request, headers })}
              addLabel="Add header"
              problems={problems}
              prefix={`${prefix}.headers`}
              namePlaceholder="X-Header"
            />
          ) : null}
          {tab === "body" ? (
            <BodyEditor
              body={request.body}
              onChange={(body) => onChange({ ...request, body })}
              problems={problems}
              prefix={prefix}
              projectId={projectId}
            />
          ) : null}
          {tab === "assertions" ? (
            <AssertionRows
              rows={request.assertions}
              onChange={(assertions) => onChange({ ...request, assertions })}
              problems={problems}
              prefix={prefix}
            />
          ) : null}
          {tab === "extract" ? (
            <ExtractionRows
              rows={request.extractions}
              onChange={(extractions) => onChange({ ...request, extractions })}
              problems={problems}
              prefix={prefix}
            />
          ) : null}
          {tab === "request" ? (
            <div className="flex max-w-md flex-col gap-3">
              {allowExtractions ? (
                <InlineInput
                  label="Step name"
                  visibleLabel
                  placeholder="Log in"
                  value={request.name}
                  onChange={(name) => onChange({ ...request, name })}
                />
              ) : null}
              <InlineInput
                label="Time limit (seconds)"
                visibleLabel
                inputMode="numeric"
                value={request.timeoutSeconds}
                onChange={(timeoutSeconds) => onChange({ ...request, timeoutSeconds })}
                error={problems[`${prefix}.timeout_seconds`]}
                hint={`1 to ${LIMITS.maxRequestTimeoutSeconds}. A request still waiting after this counts as down.`}
              />
              <Checkbox
                label="Follow redirects"
                checked={request.followRedirects}
                onChange={(followRedirects) => onChange({ ...request, followRedirects })}
                hint="Up to five hops. Every hop is checked against private addresses."
              />
            </div>
          ) : null}
        </div>
      </Tabs>
    </div>
  );
}
