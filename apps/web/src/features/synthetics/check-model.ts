/**
 * The check configuration and run evidence as the API sends them. Field names are the
 * API's, unchanged — `nexovue_platform.checks.config` and `.evidence` are the
 * definitions, and a name changed here and not there is a check the API refuses.
 */

export const CHECK_TYPES = ["api", "multistep", "heartbeat", "tcp", "dns"] as const;
export type CheckType = (typeof CHECK_TYPES)[number];

export interface NameValue {
  name: string;
  value: string;
  enabled: boolean;
}

export type RequestBody =
  | { kind: "none" }
  | { kind: "text"; content: string; content_type: string }
  | { kind: "json"; content: string }
  | { kind: "xml"; content: string }
  | { kind: "form"; fields: NameValue[] }
  | { kind: "multipart"; parts: MultipartPart[] }
  | { kind: "binary"; file_id: string; content_type: string };

export type BodyKind = RequestBody["kind"];

export type MultipartPart =
  | { kind: "field"; name: string; value: string }
  | { kind: "file"; name: string; file_id: string };

export type RequestAuth =
  | { kind: "none" }
  | { kind: "basic"; username: string; password_secret: string }
  | { kind: "bearer"; token_secret: string }
  | { kind: "api_key"; name: string; value_secret: string; location: "header" | "query" };

export type AuthKind = RequestAuth["kind"];

export const ASSERTION_SOURCES = [
  "status_code",
  "header",
  "body",
  "json_path",
  "response_time",
  "tls_days_remaining",
] as const;
export type AssertionSource = (typeof ASSERTION_SOURCES)[number];

export const COMPARISONS = [
  "equals",
  "not_equals",
  "contains",
  "not_contains",
  "less_than",
  "greater_than",
  "matches",
  "is_empty",
  "is_not_empty",
] as const;
export type Comparison = (typeof COMPARISONS)[number];

export interface Assertion {
  source: AssertionSource;
  property: string;
  comparison: Comparison;
  target: string;
}

export interface Extraction {
  name: string;
  source: "json_path" | "header" | "regex";
  expression: string;
}

export interface HttpRequest {
  name: string;
  method: string;
  url: string;
  query: NameValue[];
  headers: NameValue[];
  body: RequestBody;
  auth: RequestAuth;
  assertions: Assertion[];
  extractions: Extraction[];
  follow_redirects: boolean;
  timeout_seconds: number;
}

export const DNS_RECORD_TYPES = ["A", "AAAA", "CNAME", "MX", "NS", "TXT"] as const;
export type DnsRecordType = (typeof DNS_RECORD_TYPES)[number];

export interface DnsAssertion {
  comparison: "contains" | "not_contains" | "matches";
  target: string;
}

export type CheckConfig =
  | { type: "api"; request: HttpRequest }
  | { type: "multistep"; steps: HttpRequest[] }
  | { type: "heartbeat"; grace_seconds: number }
  | { type: "tcp"; host: string; port: number; timeout_seconds: number }
  | {
      type: "dns";
      name: string;
      record_type: DnsRecordType;
      resolver: string | null;
      assertions: DnsAssertion[];
      timeout_seconds: number;
    };

/* ------------------------------------------------------------------- limits */

/** `config.py`'s bounds, restated so a form can say so before the API does. */
export const LIMITS = {
  minIntervalSeconds: 60,
  maxIntervalSeconds: 86_400,
  maxRequestTimeoutSeconds: 30,
  maxRunSeconds: 60,
  maxSteps: 10,
  maxAssertions: 20,
  maxExtractions: 10,
  maxRows: 50,
  maxFilesPerRequest: 3,
  maxFileBytes: 3 * 1024 * 1024,
  minRetries: 1,
  maxRetries: 3,
  maxNameLength: 120,
  maxUrlLength: 2048,
} as const;

/* ----------------------------------------------------------------- evidence */

export const OUTCOMES = ["up", "degraded", "down", "misconfigured", "prober_error"] as const;
export type Outcome = (typeof OUTCOMES)[number];

export interface CapturedHeader {
  name: string;
  value: string;
}

export interface CapturedBody {
  text: string;
  size_bytes: number;
  truncated: boolean;
  binary: boolean;
}

export interface AssertionResult {
  source: string;
  property: string;
  comparison: string;
  target: string;
  /** Null when nothing was found — no such header, nothing at the path. Not "". */
  actual: string | null;
  passed: boolean;
}

export interface StepEvidence {
  position: number;
  name: string;
  target: string;
  duration_ms: number | null;
  phases_ms: Record<string, number>;
  request: {
    method: string;
    url: string;
    headers: CapturedHeader[];
    body: CapturedBody | null;
  } | null;
  response: { status_code: number; headers: CapturedHeader[]; body: CapturedBody | null } | null;
  answers: string[];
  assertions: AssertionResult[];
  extracted: string[];
  passed: boolean;
  error: string | null;
}

export interface RunEvidence {
  run_id: string;
  check_id: string | null;
  check_type: string;
  location: string;
  attempt: number;
  final: boolean;
  maintenance: boolean;
  outcome: Outcome;
  started_at: string;
  /** Null when nothing was timed: a heartbeat, or a run refused before its request. */
  duration_ms: number | null;
  status_code: number | null;
  tls_days_remaining: number | null;
  steps: StepEvidence[];
  error: string | null;
}

/* ------------------------------------------------------------------- states */

export type DisplayedState =
  | "up"
  | "degraded"
  | "down"
  | "misconfigured"
  | "paused"
  | "maintenance";
