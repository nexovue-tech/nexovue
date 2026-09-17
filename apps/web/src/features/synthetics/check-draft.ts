/**
 * The check form's own model, and the one translation between it and the API.
 *
 * Every number is kept as the text the person typed until the moment it is sent: a
 * field emptied mid-edit is "", not 0, and a form that turned it into 0 would save a
 * timeout nobody chose. Rows carry a `key` from `crypto.randomUUID()` so a removed row
 * never hands its input's state to the row that took its place.
 *
 * Field problems are keyed by the API's own paths (`config.request.url`,
 * `config.steps.1.assertions.0.target`), so a message from the client and one from a
 * 422 land under the same input.
 */

import type {
  Assertion,
  AssertionSource,
  AuthKind,
  BodyKind,
  CheckConfig,
  CheckType,
  Comparison,
  DnsAssertion,
  DnsRecordType,
  Extraction,
  HttpRequest,
  NameValue,
  RequestAuth,
  RequestBody,
} from "./check-model";
import { LIMITS } from "./check-model";

export interface RowDraft {
  key: string;
  name: string;
  value: string;
  enabled: boolean;
}

export interface AssertionDraft {
  key: string;
  source: AssertionSource;
  property: string;
  comparison: Comparison;
  target: string;
}

export interface ExtractionDraft {
  key: string;
  name: string;
  source: Extraction["source"];
  expression: string;
}

export interface PartDraft {
  key: string;
  kind: "field" | "file";
  name: string;
  value: string;
  fileId: string;
  fileName: string;
}

export interface BodyDraft {
  kind: BodyKind;
  text: string;
  contentType: string;
  fields: RowDraft[];
  parts: PartDraft[];
  fileId: string;
  fileName: string;
}

export interface AuthDraft {
  kind: AuthKind;
  username: string;
  passwordSecret: string;
  tokenSecret: string;
  keyName: string;
  valueSecret: string;
  location: "header" | "query";
}

export interface RequestDraft {
  key: string;
  name: string;
  method: string;
  url: string;
  query: RowDraft[];
  headers: RowDraft[];
  body: BodyDraft;
  auth: AuthDraft;
  assertions: AssertionDraft[];
  extractions: ExtractionDraft[];
  followRedirects: boolean;
  timeoutSeconds: string;
}

export interface DnsAssertionDraft {
  key: string;
  comparison: DnsAssertion["comparison"];
  target: string;
}

export interface CheckDraft {
  kind: CheckType;
  name: string;
  intervalSeconds: string;
  /** Empty means every location. */
  locations: string[];
  degradedMs: string;
  failingMs: string;
  retries: string;
  request: RequestDraft;
  steps: RequestDraft[];
  graceSeconds: string;
  host: string;
  port: string;
  dnsName: string;
  recordType: DnsRecordType;
  resolver: string;
  dnsAssertions: DnsAssertionDraft[];
  timeoutSeconds: string;
}

export type FieldProblems = Record<string, string>;

export const COMMON_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] as const;

const DEFAULT_TIMEOUT_SECONDS = "10";
const DEFAULT_GRACE_SECONDS = "300";
const DEFAULT_INTERVAL_SECONDS = "60";
const DEFAULT_RETRIES = "1";
/** What a body kind is sent as until someone types another content type. */
const DEFAULT_CONTENT_TYPES: Readonly<Partial<Record<BodyKind, string>>> = {
  text: "text/plain",
  binary: "application/octet-stream",
};
const SHORT_FILE_ID_LENGTH = 8;
const METHOD_TOKEN = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;
const LINE_BREAK = /[\r\n\0]/;
const URL_START = /^(https?:\/\/|\{\{)/i;
const NUMERIC_SOURCES: ReadonlySet<AssertionSource> = new Set([
  "status_code",
  "response_time",
  "tls_days_remaining",
]);
const TARGETLESS: ReadonlySet<Comparison> = new Set(["is_empty", "is_not_empty"]);
const ORDERING: ReadonlySet<Comparison> = new Set(["less_than", "greater_than"]);
const VARIABLE_NAME = /^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/;
const CHECK_TYPE_SEGMENTS: ReadonlySet<string> = new Set([
  "api",
  "multistep",
  "heartbeat",
  "tcp",
  "dns",
]);
const KIND_SEGMENTS: ReadonlySet<string> = new Set([
  "none",
  "text",
  "json",
  "xml",
  "form",
  "multipart",
  "binary",
  "basic",
  "bearer",
  "api_key",
  "field",
  "file",
]);

export function newKey(): string {
  return crypto.randomUUID();
}

export function emptyRow(): RowDraft {
  return { key: newKey(), name: "", value: "", enabled: true };
}

export function emptyAssertion(): AssertionDraft {
  return {
    key: newKey(),
    source: "status_code",
    property: "",
    comparison: "less_than",
    target: "400",
  };
}

export function emptyExtraction(): ExtractionDraft {
  return { key: newKey(), name: "", source: "json_path", expression: "$." };
}

export function emptyPart(kind: PartDraft["kind"] = "field"): PartDraft {
  return { key: newKey(), kind, name: "", value: "", fileId: "", fileName: "" };
}

/** How many files this body already sends, against `LIMITS.maxFilesPerRequest`. */
export function attachedFileCount(body: BodyDraft): number {
  if (body.kind === "binary") return body.fileId ? 1 : 0;
  if (body.kind !== "multipart") return 0;
  return body.parts.filter((part) => part.kind === "file" && part.fileId).length;
}

export function emptyRequest(): RequestDraft {
  return {
    key: newKey(),
    name: "",
    method: "GET",
    url: "",
    query: [],
    headers: [],
    body: {
      kind: "none",
      text: "",
      contentType: "",
      fields: [],
      parts: [],
      fileId: "",
      fileName: "",
    },
    auth: {
      kind: "none",
      username: "",
      passwordSecret: "",
      tokenSecret: "",
      keyName: "X-API-Key",
      valueSecret: "",
      location: "header",
    },
    assertions: [emptyAssertion()],
    extractions: [],
    followRedirects: true,
    timeoutSeconds: DEFAULT_TIMEOUT_SECONDS,
  };
}

export function emptyDraft(kind: CheckType): CheckDraft {
  return {
    kind,
    name: "",
    intervalSeconds: kind === "heartbeat" ? "3600" : DEFAULT_INTERVAL_SECONDS,
    locations: [],
    degradedMs: "",
    failingMs: "",
    retries: DEFAULT_RETRIES,
    request: emptyRequest(),
    steps: [emptyRequest(), emptyRequest()],
    graceSeconds: DEFAULT_GRACE_SECONDS,
    host: "",
    port: "443",
    dnsName: "",
    recordType: "A",
    resolver: "",
    dnsAssertions: [],
    timeoutSeconds: DEFAULT_TIMEOUT_SECONDS,
  };
}

export function defaultContentType(kind: BodyKind): string {
  return DEFAULT_CONTENT_TYPES[kind] ?? "";
}

/**
 * Another body kind. A content type that was only the old kind's default becomes the new
 * kind's, so a binary body is never sent as text/plain; one somebody typed is kept.
 */
export function withBodyKind(body: BodyDraft, kind: BodyKind): BodyDraft {
  const defaults: readonly string[] = Object.values(DEFAULT_CONTENT_TYPES);
  const typed = body.contentType !== "" && !defaults.includes(body.contentType);
  return { ...body, kind, contentType: typed ? body.contentType : defaultContentType(kind) };
}

/** A stored definition keeps a file's id, not its name, so a file read back is named by its id. */
export function attachedFileLabel(fileId: string, fileName: string): string {
  return fileName || `attached file (${fileId.slice(0, SHORT_FILE_ID_LENGTH)})`;
}

/* ----------------------------------------------------------- draft ↔ config */

function rowsFrom(pairs: readonly NameValue[]): RowDraft[] {
  return pairs.map((pair) => ({ key: newKey(), ...pair }));
}

/** A row with neither name nor value is an unused line in the table, and is not sent. */
function isSent(row: RowDraft): boolean {
  return row.name !== "" || row.value !== "";
}

function pairsFrom(rows: readonly RowDraft[]): NameValue[] {
  return rows.filter(isSent).map(({ name, value, enabled }) => ({ name, value, enabled }));
}

export function requestDraftFrom(request: HttpRequest): RequestDraft {
  const draft = emptyRequest();
  const body = request.body;
  const auth = request.auth;
  return {
    ...draft,
    name: request.name,
    method: request.method,
    url: request.url,
    query: rowsFrom(request.query),
    headers: rowsFrom(request.headers),
    body: {
      ...draft.body,
      kind: body.kind,
      text: body.kind === "text" || body.kind === "json" || body.kind === "xml" ? body.content : "",
      contentType:
        body.kind === "text" || body.kind === "binary" ? body.content_type : draft.body.contentType,
      fields: body.kind === "form" ? rowsFrom(body.fields) : [],
      parts:
        body.kind === "multipart"
          ? body.parts.map((part) =>
              part.kind === "file"
                ? { ...emptyPart("file"), name: part.name, fileId: part.file_id }
                : { ...emptyPart("field"), name: part.name, value: part.value },
            )
          : [],
      fileId: body.kind === "binary" ? body.file_id : "",
    },
    auth: {
      ...draft.auth,
      kind: auth.kind,
      username: auth.kind === "basic" ? auth.username : "",
      passwordSecret: auth.kind === "basic" ? auth.password_secret : "",
      tokenSecret: auth.kind === "bearer" ? auth.token_secret : "",
      keyName: auth.kind === "api_key" ? auth.name : draft.auth.keyName,
      valueSecret: auth.kind === "api_key" ? auth.value_secret : "",
      location: auth.kind === "api_key" ? auth.location : "header",
    },
    assertions: request.assertions.map((assertion) => ({ key: newKey(), ...assertion })),
    extractions: request.extractions.map((extraction) => ({ key: newKey(), ...extraction })),
    followRedirects: request.follow_redirects,
    timeoutSeconds: String(request.timeout_seconds),
  };
}

function bodyFrom(draft: BodyDraft): RequestBody {
  switch (draft.kind) {
    case "none":
      return { kind: "none" };
    case "text":
      return {
        kind: "text",
        content: draft.text,
        content_type: draft.contentType || defaultContentType("text"),
      };
    case "json":
      return { kind: "json", content: draft.text };
    case "xml":
      return { kind: "xml", content: draft.text };
    case "form":
      return { kind: "form", fields: pairsFrom(draft.fields) };
    case "multipart":
      return {
        kind: "multipart",
        parts: draft.parts.map((part) =>
          part.kind === "file"
            ? { kind: "file", name: part.name, file_id: part.fileId }
            : { kind: "field", name: part.name, value: part.value },
        ),
      };
    case "binary":
      return {
        kind: "binary",
        file_id: draft.fileId,
        content_type: draft.contentType || defaultContentType("binary"),
      };
  }
}

function authFrom(draft: AuthDraft): RequestAuth {
  switch (draft.kind) {
    case "none":
      return { kind: "none" };
    case "basic":
      return { kind: "basic", username: draft.username, password_secret: draft.passwordSecret };
    case "bearer":
      return { kind: "bearer", token_secret: draft.tokenSecret };
    case "api_key":
      return {
        kind: "api_key",
        name: draft.keyName,
        value_secret: draft.valueSecret,
        location: draft.location,
      };
  }
}

export function requestFrom(draft: RequestDraft, allowExtractions: boolean): HttpRequest {
  return {
    name: draft.name,
    method: draft.method.trim() || "GET",
    url: draft.url.trim(),
    query: pairsFrom(draft.query),
    headers: pairsFrom(draft.headers),
    body: bodyFrom(draft.body),
    auth: authFrom(draft.auth),
    assertions: draft.assertions.map(
      ({ source, property, comparison, target }): Assertion => ({
        source,
        property: sourceTakesProperty(source) ? property : "",
        comparison,
        target: TARGETLESS.has(comparison) ? "" : target,
      }),
    ),
    extractions: allowExtractions
      ? draft.extractions.map(({ name, source, expression }) => ({ name, source, expression }))
      : [],
    follow_redirects: draft.followRedirects,
    timeout_seconds: Number(draft.timeoutSeconds),
  };
}

export function configFrom(draft: CheckDraft): CheckConfig {
  switch (draft.kind) {
    case "api":
      return { type: "api", request: requestFrom(draft.request, false) };
    case "multistep":
      return { type: "multistep", steps: draft.steps.map((step) => requestFrom(step, true)) };
    case "heartbeat":
      return { type: "heartbeat", grace_seconds: Number(draft.graceSeconds) };
    case "tcp":
      return {
        type: "tcp",
        host: draft.host.trim(),
        port: Number(draft.port),
        timeout_seconds: Number(draft.timeoutSeconds),
      };
    case "dns":
      return {
        type: "dns",
        name: draft.dnsName.trim(),
        record_type: draft.recordType,
        resolver: draft.resolver.trim() || null,
        assertions: draft.dnsAssertions.map(({ comparison, target }) => ({ comparison, target })),
        timeout_seconds: Number(draft.timeoutSeconds),
      };
  }
}

export interface DraftSource {
  name: string;
  config: CheckConfig;
  interval_seconds: number;
  locations: string[];
  degraded_ms: number | null;
  failing_ms: number | null;
  retries: number;
}

export function draftFrom(check: DraftSource): CheckDraft {
  const draft = emptyDraft(check.config.type);
  const config = check.config;
  const shared: CheckDraft = {
    ...draft,
    name: check.name,
    intervalSeconds: String(check.interval_seconds),
    locations: [...check.locations],
    degradedMs: check.degraded_ms === null ? "" : String(check.degraded_ms),
    failingMs: check.failing_ms === null ? "" : String(check.failing_ms),
    retries: String(check.retries),
  };
  switch (config.type) {
    case "api":
      return { ...shared, request: requestDraftFrom(config.request) };
    case "multistep":
      return { ...shared, steps: config.steps.map(requestDraftFrom) };
    case "heartbeat":
      return { ...shared, graceSeconds: String(config.grace_seconds) };
    case "tcp":
      return {
        ...shared,
        host: config.host,
        port: String(config.port),
        timeoutSeconds: String(config.timeout_seconds),
      };
    case "dns":
      return {
        ...shared,
        dnsName: config.name,
        recordType: config.record_type,
        resolver: config.resolver ?? "",
        dnsAssertions: config.assertions.map((assertion) => ({ key: newKey(), ...assertion })),
        timeoutSeconds: String(config.timeout_seconds),
      };
  }
}

/** What a save sends. Thresholds are cleared by sending null, never by omitting them. */
export function inputFrom(draft: CheckDraft) {
  const measured = draft.kind !== "heartbeat";
  return {
    name: draft.name.trim(),
    config: configFrom(draft),
    interval_seconds: Number(draft.intervalSeconds),
    locations: measured ? draft.locations : [],
    degraded_ms: measured && draft.degradedMs !== "" ? Number(draft.degradedMs) : null,
    failing_ms: measured && draft.failingMs !== "" ? Number(draft.failingMs) : null,
    retries: Number(draft.retries),
  };
}

/** Dirty means different from what is stored, never that a field was touched. */
export function sameCheck(left: CheckDraft, right: CheckDraft): boolean {
  return JSON.stringify(inputFrom(left)) === JSON.stringify(inputFrom(right));
}

/** The name a check gets when nobody typed one: what it calls. */
export function suggestedName(draft: CheckDraft): string {
  const first = draft.kind === "multistep" ? draft.steps[0] : draft.request;
  switch (draft.kind) {
    case "api":
    case "multistep": {
      const url = first?.url.trim() ?? "";
      const withoutScheme = url.replace(/^https?:\/\//i, "");
      return withoutScheme
        ? `${first?.method ?? "GET"} ${withoutScheme}`.slice(0, LIMITS.maxNameLength)
        : "";
    }
    case "tcp":
      return draft.host ? `${draft.host}:${draft.port}` : "";
    case "dns":
      return draft.dnsName ? `${draft.dnsName} ${draft.recordType}` : "";
    case "heartbeat":
      return "";
  }
}

/* --------------------------------------------------------------- validation */

export function sourceTakesProperty(source: AssertionSource): boolean {
  return source === "header" || source === "json_path";
}

/**
 * An assertion pointed at something else.
 *
 * The comparison follows the new source, and so does the expected value. A row that
 * compared a response code and is then pointed at a header would otherwise keep the
 * number it was comparing against, and "Content-Type equals 4xx" is a check somebody
 * saves by accident. A value is kept only when both sources want the same kind of one.
 */
export function withAssertionSource(row: AssertionDraft, source: AssertionSource): AssertionDraft {
  const comparisons = comparisonsFor(source);
  // Kept only between two text sources, where "application/json" means the same
  // thing either side. Every numeric source measures something different — a
  // response code, milliseconds, days — so a number never carries across.
  const sameKind = !NUMERIC_SOURCES.has(source) && !NUMERIC_SOURCES.has(row.source);
  return {
    ...row,
    source,
    property: sourceTakesProperty(source) ? row.property : "",
    comparison: comparisons.includes(row.comparison)
      ? row.comparison
      : (comparisons[0] ?? "equals"),
    target: sameKind ? row.target : "",
  };
}

export function comparisonsFor(source: AssertionSource): readonly Comparison[] {
  if (NUMERIC_SOURCES.has(source)) return ["equals", "not_equals", "less_than", "greater_than"];
  if (source === "json_path")
    return [
      "equals",
      "not_equals",
      "contains",
      "not_contains",
      "less_than",
      "greater_than",
      "matches",
      "is_empty",
      "is_not_empty",
    ];
  return [
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "matches",
    "is_empty",
    "is_not_empty",
  ];
}

export function comparisonTakesTarget(comparison: Comparison): boolean {
  return !TARGETLESS.has(comparison);
}

function whole(text: string): number | undefined {
  if (!/^\d+$/.test(text.trim())) return undefined;
  return Number(text);
}

/** Every `{{name}}` and `{{secret.NAME}}` written in a text, in the order they appear. */
const REFERENCE = /\{\{\s*([^{}\s]+)\s*\}\}/g;

export interface KnownNames {
  secrets: readonly string[];
  variables: readonly string[];
}

/**
 * The fields of one request that may carry a reference, each with the field path
 * an error would be shown against.
 *
 * The API refuses an unknown name too, but it can only name the request it was in
 * (`config.request`), which puts the message under the URL however deep in the body
 * the reference actually was. Checking here is what puts it on the field.
 */
function referenceBearingFields(
  draft: RequestDraft,
  prefix: string,
): Array<{ path: string; text: string }> {
  const fields = [{ path: `${prefix}.url`, text: draft.url }];
  // Names as well as values: `request_builder.build` fills both sides of every pair,
  // so `{{secret.KEY}}` as a header *name* is substituted and can be undefined too.
  for (const [rows, name] of [
    [draft.query, "query"],
    [draft.headers, "headers"],
  ] as const) {
    rows.forEach((row, index) => {
      fields.push({ path: `${prefix}.${name}.${index}.name`, text: row.name });
      fields.push({ path: `${prefix}.${name}.${index}.value`, text: row.value });
    });
  }
  if (draft.body.kind === "text" || draft.body.kind === "json" || draft.body.kind === "xml") {
    fields.push({ path: `${prefix}.body.content`, text: draft.body.text });
  }
  if (draft.body.kind === "form") {
    draft.body.fields.forEach((row, index) => {
      fields.push({ path: `${prefix}.body.fields.${index}.name`, text: row.name });
      fields.push({ path: `${prefix}.body.fields.${index}.value`, text: row.value });
    });
  }
  if (draft.body.kind === "multipart") {
    draft.body.parts.forEach((part, index) => {
      if (part.kind === "field") {
        fields.push({ path: `${prefix}.body.parts.${index}.name`, text: part.name });
        fields.push({ path: `${prefix}.body.parts.${index}.value`, text: part.value });
      }
    });
  }
  draft.assertions.forEach((assertion, index) => {
    fields.push({ path: `${prefix}.assertions.${index}.target`, text: assertion.target });
  });
  return fields;
}

/** Names a check writes but the project does not define, against the field that wrote them. */
function referenceProblems(
  draft: RequestDraft,
  prefix: string,
  known: KnownNames,
  problems: FieldProblems,
): void {
  const secrets = new Set(known.secrets);
  const variables = new Set(known.variables);
  for (const field of referenceBearingFields(draft, prefix)) {
    for (const match of field.text.matchAll(REFERENCE)) {
      const reference = match[1];
      const secret = reference.startsWith("secret.");
      const name = secret ? reference.slice("secret.".length) : reference;
      if (secret ? secrets.has(name) : variables.has(name)) continue;
      problems[field.path] = secret
        ? `This project has no secret named ${name}. Store it in project settings, or correct the name.`
        : `This project has no variable named ${name}. Add it in project settings, or correct the name.`;
      break; // one message per field: the first unknown name is the one to fix
    }
  }
}

function requestProblems(draft: RequestDraft, prefix: string, problems: FieldProblems): void {
  if (!METHOD_TOKEN.test(draft.method.trim() || "GET")) {
    problems[`${prefix}.method`] = "A method is one word of letters, digits and !#$%&'*+-.^_`|~.";
  }
  const url = draft.url.trim();
  if (!url) problems[`${prefix}.url`] = "Enter the URL to call.";
  else if (!URL_START.test(url)) problems[`${prefix}.url`] = "Start with http:// or https://.";
  else if (/\s/.test(url)) problems[`${prefix}.url`] = "A URL cannot contain spaces.";
  else if (url.length > LIMITS.maxUrlLength) {
    problems[`${prefix}.url`] = `At most ${LIMITS.maxUrlLength} characters.`;
  }

  rowProblemsOf(draft.query, `${prefix}.query`, "Name the parameter.", problems);
  rowProblemsOf(draft.headers, `${prefix}.headers`, "Name the header.", problems);
  draft.headers.forEach((row, index) => {
    if (row.name && !METHOD_TOKEN.test(row.name)) {
      problems[`${prefix}.headers.${index}.name`] =
        "A header name has no spaces or punctuation like : or /.";
    }
  });

  const timeout = whole(draft.timeoutSeconds);
  if (timeout === undefined || timeout < 1 || timeout > LIMITS.maxRequestTimeoutSeconds) {
    problems[`${prefix}.timeout_seconds`] =
      `Between 1 and ${LIMITS.maxRequestTimeoutSeconds} seconds.`;
  }

  if (draft.assertions.length === 0) {
    problems[`${prefix}.assertions`] =
      "Add at least one assertion, or a failure could never be seen.";
  }
  draft.assertions.forEach((assertion, index) => {
    const at = `${prefix}.assertions.${index}`;
    if (sourceTakesProperty(assertion.source) && !assertion.property.trim()) {
      problems[`${at}.property`] =
        assertion.source === "header" ? "Which header?" : "Which JSON path? For example $.order.id";
    }
    if (
      assertion.source === "json_path" &&
      assertion.property &&
      !assertion.property.startsWith("$")
    ) {
      problems[`${at}.property`] = "A JSON path starts with $.";
    }
    const needsNumber = NUMERIC_SOURCES.has(assertion.source) || ORDERING.has(assertion.comparison);
    if (needsNumber && Number.isNaN(Number.parseFloat(assertion.target))) {
      problems[`${at}.target`] = "A number.";
    } else if (
      comparisonTakesTarget(assertion.comparison) &&
      assertion.target === "" &&
      !["equals", "not_equals"].includes(assertion.comparison)
    ) {
      problems[`${at}.target`] = "What to compare against.";
    }
  });

  if (draft.body.kind === "form") {
    rowProblemsOf(draft.body.fields, `${prefix}.body.fields`, "Name the field.", problems);
  }
  if (draft.body.kind === "binary" && !draft.body.fileId) {
    problems[`${prefix}.body`] = "Attach the file to send.";
  }
  if (draft.body.kind === "multipart") {
    const files = draft.body.parts.filter((part) => part.kind === "file");
    if (files.length > LIMITS.maxFilesPerRequest) {
      problems[`${prefix}.body`] = `A request sends at most ${LIMITS.maxFilesPerRequest} files.`;
    }
    draft.body.parts.forEach((part, index) => {
      if (!part.name) problems[`${prefix}.body.parts.${index}.name`] = "Name the part.";
      if (part.kind === "file" && !part.fileId) {
        problems[`${prefix}.body.parts.${index}.file_id`] = "Attach a file.";
      }
    });
  }

  const auth = draft.auth;
  if (auth.kind === "basic") {
    if (!auth.username) problems[`${prefix}.auth.username`] = "Enter the username.";
    if (!auth.passwordSecret)
      problems[`${prefix}.auth.password_secret`] = "Choose the secret holding the password.";
  } else if (auth.kind === "bearer" && !auth.tokenSecret) {
    problems[`${prefix}.auth.token_secret`] = "Choose the secret holding the token.";
  } else if (auth.kind === "api_key") {
    if (!auth.keyName) problems[`${prefix}.auth.name`] = "Name the header or parameter.";
    if (!auth.valueSecret)
      problems[`${prefix}.auth.value_secret`] = "Choose the secret holding the key.";
  }

  draft.extractions.forEach((extraction, index) => {
    if (!VARIABLE_NAME.test(extraction.name)) {
      problems[`${prefix}.extractions.${index}.name`] =
        "Letters, digits, _ . -; starting with a letter.";
    }
    if (!extraction.expression)
      problems[`${prefix}.extractions.${index}.expression`] = "What to take.";
  });
}

/** Every sent row needs a name: the API refuses a nameless one, and a line break in either. */
function rowProblemsOf(
  rows: readonly RowDraft[],
  listPath: string,
  unnamed: string,
  problems: FieldProblems,
): void {
  rows.forEach((row, index) => {
    if (!isSent(row)) return;
    if (!row.name) problems[`${listPath}.${index}.name`] = unnamed;
    else if (LINE_BREAK.test(row.name)) problems[`${listPath}.${index}.name`] = "No line breaks.";
    if (LINE_BREAK.test(row.value)) problems[`${listPath}.${index}.value`] = "No line breaks.";
  });
}

export function validate(draft: CheckDraft, known?: KnownNames): FieldProblems {
  const problems: FieldProblems = {};
  if (draft.name.trim().length > LIMITS.maxNameLength) {
    problems.name = `At most ${LIMITS.maxNameLength} characters.`;
  }
  const interval = whole(draft.intervalSeconds);
  if (
    interval === undefined ||
    interval < LIMITS.minIntervalSeconds ||
    interval > LIMITS.maxIntervalSeconds
  ) {
    problems.interval_seconds = "Between 60 seconds and 24 hours (86400 seconds).";
  }

  switch (draft.kind) {
    case "api":
      requestProblems(draft.request, "config.request", problems);
      if (known) referenceProblems(draft.request, "config.request", known, problems);
      break;
    case "multistep": {
      if (draft.steps.length === 0) problems["config.steps"] = "Add a request.";
      if (draft.steps.length > LIMITS.maxSteps)
        problems["config.steps"] = `At most ${LIMITS.maxSteps} requests.`;
      draft.steps.forEach((step, index) => {
        requestProblems(step, `config.steps.${index}`, problems);
        // A later request may use what an earlier one extracted, so those names
        // count as defined even though the project never heard of them.
        if (known) {
          const extracted = draft.steps
            .slice(0, index)
            .flatMap((earlier) => earlier.extractions.map((extraction) => extraction.name));
          referenceProblems(
            step,
            `config.steps.${index}`,
            { secrets: known.secrets, variables: [...known.variables, ...extracted] },
            problems,
          );
        }
      });
      const total = draft.steps.reduce((sum, step) => sum + (whole(step.timeoutSeconds) ?? 0), 0);
      if (total > LIMITS.maxRunSeconds) {
        problems["config.steps"] =
          `The requests' time limits add up to ${total}s; at most ${LIMITS.maxRunSeconds}s.`;
      }
      break;
    }
    case "heartbeat": {
      const grace = whole(draft.graceSeconds);
      if (grace === undefined || grace > LIMITS.maxIntervalSeconds) {
        problems["config.grace_seconds"] = "Between 0 and 86400 seconds.";
      }
      break;
    }
    case "tcp": {
      if (!draft.host.trim()) problems["config.host"] = "Enter a host name or address.";
      else if (/[/\s]|:\/\//.test(draft.host))
        problems["config.host"] = "Only the host: no scheme, path or port.";
      const port = whole(draft.port);
      if (port === undefined || port < 1 || port > 65_535)
        problems["config.port"] = "A port from 1 to 65535.";
      break;
    }
    case "dns":
      if (!draft.dnsName.trim()) problems["config.name"] = "Enter the domain name.";
      if (draft.resolver && !/^[0-9a-fA-F.:[\]]+$/.test(draft.resolver)) {
        problems["config.resolver"] = "A resolver is an IP address, such as 1.1.1.1.";
      }
      draft.dnsAssertions.forEach((assertion, index) => {
        if (!assertion.target)
          problems[`config.assertions.${index}.target`] = "What the answer should be.";
      });
      break;
  }

  if (draft.kind !== "api" && draft.kind !== "multistep") {
    const timeout = whole(draft.timeoutSeconds);
    if (draft.kind !== "heartbeat" && (timeout === undefined || timeout < 1 || timeout > 30)) {
      problems["config.timeout_seconds"] = "Between 1 and 30 seconds.";
    }
  }
  if (draft.kind !== "heartbeat") {
    const degraded = draft.degradedMs === "" ? undefined : whole(draft.degradedMs);
    const failing = draft.failingMs === "" ? undefined : whole(draft.failingMs);
    if (draft.degradedMs !== "" && (degraded === undefined || degraded < 1)) {
      problems.degraded_ms = "Whole milliseconds, or leave empty.";
    }
    if (draft.failingMs !== "" && (failing === undefined || failing < 1)) {
      problems.failing_ms = "Whole milliseconds, or leave empty.";
    }
    if (degraded !== undefined && failing !== undefined && degraded >= failing) {
      problems.failing_ms = "Must be above the degraded threshold.";
    }
    const retries = whole(draft.retries);
    if (retries === undefined || retries < LIMITS.minRetries || retries > LIMITS.maxRetries) {
      problems.retries = "1, 2 or 3.";
    }
  }
  return problems;
}

/**
 * The API's locations, rewritten to the form's paths. Pydantic names the branch a
 * discriminated union took — `config.api.request.body.json.content` — and the form does
 * not, so those segments are dropped, and so is Pydantic's `Value error, ` prefix. Row
 * numbers are the sent rows', so they are mapped back to the rows on screen.
 */
export function problemsFromApi(
  fieldErrors: Record<string, string>,
  sent: CheckDraft,
): FieldProblems {
  const problems: FieldProblems = {};
  for (const [location, reason] of Object.entries(fieldErrors)) {
    const segments = location.split(".");
    const kept = segments.filter((segment, index) => {
      const previous = segments[index - 1];
      if (previous === "config") return !CHECK_TYPE_SEGMENTS.has(segment);
      if (previous === "body" || previous === "auth") return !KIND_SEGMENTS.has(segment);
      if (segments[index - 2] === "parts" && /^\d+$/.test(previous ?? "")) {
        return segment !== "field" && segment !== "file";
      }
      return true;
    });
    const path = onScreenRowPath(kept.join("."), sent);
    problems[path] = reason.replace(/^Value error, /, "");
  }
  return problems;
}

const SENT_ROW_PATH =
  /^config\.(request|steps\.(\d+))\.(query|headers|body\.fields)\.(\d+)(\..+)?$/;

/** `pairsFrom` leaves unused rows out, so the API's row N is the Nth row that was sent. */
function onScreenRowPath(path: string, sent: CheckDraft): string {
  const match = SENT_ROW_PATH.exec(path);
  if (!match) return path;
  const [, owner = "", step, list = "", sentIndex = "", rest = ""] = match;
  const request = step === undefined ? sent.request : sent.steps[Number(step)];
  if (!request) return path;
  const rows =
    list === "query" ? request.query : list === "headers" ? request.headers : request.body.fields;
  const onScreen = rows.flatMap((row, index) => (isSent(row) ? [index] : []))[Number(sentIndex)];
  return onScreen === undefined ? path : `config.${owner}.${list}.${onScreen}${rest}`;
}

/** Problems about a whole row, such as `config.request.assertions.2`, by row index. */
export function rowProblems(problems: FieldProblems, listPath: string): Record<number, string> {
  const byRow: Record<number, string> = {};
  for (const [path, reason] of Object.entries(problems)) {
    if (!path.startsWith(`${listPath}.`)) continue;
    const rest = path.slice(listPath.length + 1);
    if (/^\d+$/.test(rest)) byRow[Number(rest)] = reason;
  }
  return byRow;
}

/** Every problem at or under `prefix` — how a tab knows it holds one. */
export function problemsUnder(problems: FieldProblems, prefix: string): string[] {
  return Object.entries(problems)
    .filter(([path]) => path === prefix || path.startsWith(`${prefix}.`))
    .map(([, reason]) => reason);
}
