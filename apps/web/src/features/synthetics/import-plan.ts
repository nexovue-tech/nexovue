import { fieldProblemsOf } from "./api-problems";
import type { Assertion, CheckConfig, HttpRequest } from "./check-model";
import { LIMITS } from "./check-model";
import type {
  ImportConfirmBody,
  ImportItem,
  ImportPlan,
  ImportSource,
  NamedValue,
} from "./import-api";

/**
 * What the Import screen decides without asking the API: which checks are chosen, which
 * values are still missing, what Create sends, and where a refusal belongs.
 *
 * Only the values a *chosen* check uses are required or sent. A secret found in a request
 * the person deselected is not theirs to fill in, and storing it anyway would keep a
 * credential nothing uses.
 */

/** `checksecrets.schemas.MIN_SECRET_LENGTH`: shorter would redact unrelated text in evidence. */
export const MIN_SECRET_LENGTH = 4;
export const DEFAULT_INTERVAL_SECONDS = LIMITS.minIntervalSeconds;

const REFERENCE = /\{\{\s*([^{}\s]+)\s*\}\}/g;
const SECRET_PREFIX = "secret.";
/** `checks.3.config.request.url` → item 3, the rest is the field inside it. */
const CHECK_FIELD = /^checks\.(\d+)(?:\.(.*))?$/;

export const SOURCE_NAMES: Record<ImportSource, string> = {
  curl: "cURL",
  openapi: "OpenAPI",
  postman: "Postman",
};

export interface DraftItem {
  name: string;
  selected: boolean;
}

/** What the person has changed on top of a plan. */
export interface ImportDraft {
  items: Record<string, DraftItem>;
  secretValues: Record<string, string>;
  variableValues: Record<string, string>;
}

export interface ImportProblems {
  /** One line per problem, for the summary beside Create. */
  summary: string[];
  items: Record<string, string>;
  secrets: Record<string, string>;
  variables: Record<string, string>;
}

/**
 * A draft for a plan. Carries over what was typed into a previous draft, so choosing
 * another OpenAPI server does not throw away secrets already entered.
 */
export function draftFromPlan(plan: ImportPlan, previous?: ImportDraft): ImportDraft {
  const items: Record<string, DraftItem> = {};
  for (const item of plan.items) {
    items[item.key] = previous?.items[item.key] ?? { name: item.name, selected: item.selected };
  }
  const secretValues: Record<string, string> = {};
  for (const secret of plan.secrets) {
    secretValues[secret.name] = previous?.secretValues[secret.name] ?? secret.value;
  }
  const variableValues: Record<string, string> = {};
  for (const variable of plan.variables) {
    variableValues[variable.name] = previous?.variableValues[variable.name] ?? variable.value;
  }
  return { items, secretValues, variableValues };
}

export function withItem(draft: ImportDraft, key: string, change: Partial<DraftItem>): ImportDraft {
  const current = draft.items[key];
  if (!current) return draft;
  return { ...draft, items: { ...draft.items, [key]: { ...current, ...change } } };
}

export function withSecretValue(draft: ImportDraft, name: string, value: string): ImportDraft {
  return { ...draft, secretValues: { ...draft.secretValues, [name]: value } };
}

export function withVariableValue(draft: ImportDraft, name: string, value: string): ImportDraft {
  return { ...draft, variableValues: { ...draft.variableValues, [name]: value } };
}

export function isSelected(draft: ImportDraft, item: ImportItem): boolean {
  return draft.items[item.key]?.selected ?? item.selected;
}

export function selectedItems(plan: ImportPlan, draft: ImportDraft): ImportItem[] {
  return plan.items.filter((item) => isSelected(draft, item));
}

/* --------------------------------------------------------------- references */

function requestsOf(config: CheckConfig): HttpRequest[] {
  if (config.type === "api") return [config.request];
  if (config.type === "multistep") return config.steps;
  return [];
}

/** Every string a `{{reference}}` may appear in — the same places the API looks. */
function textsOf(request: HttpRequest): string[] {
  const texts = [request.url];
  for (const pair of [...request.query, ...request.headers]) texts.push(pair.name, pair.value);
  const body = request.body;
  if (body.kind === "text" || body.kind === "json" || body.kind === "xml") texts.push(body.content);
  if (body.kind === "form") for (const field of body.fields) texts.push(field.name, field.value);
  if (body.kind === "multipart") {
    for (const part of body.parts) if (part.kind === "field") texts.push(part.value);
  }
  if (request.auth.kind === "basic") texts.push(request.auth.username);
  for (const assertion of request.assertions) texts.push(assertion.target);
  return texts;
}

interface References {
  secrets: Set<string>;
  variables: Set<string>;
}

export function referencesOf(config: CheckConfig): References {
  const secrets = new Set<string>();
  const variables = new Set<string>();
  for (const request of requestsOf(config)) {
    for (const text of textsOf(request)) {
      for (const match of text.matchAll(REFERENCE)) {
        const reference = match[1] ?? "";
        if (reference.startsWith(SECRET_PREFIX) && reference.length > SECRET_PREFIX.length) {
          secrets.add(reference.slice(SECRET_PREFIX.length));
        } else {
          variables.add(reference);
        }
      }
    }
    const auth = request.auth;
    if (auth.kind === "basic") secrets.add(auth.password_secret);
    if (auth.kind === "bearer") secrets.add(auth.token_secret);
    if (auth.kind === "api_key") secrets.add(auth.value_secret);
  }
  return { secrets, variables };
}

/** The detected secrets and variables the chosen checks use, in the plan's order. */
export function valuesInUse(plan: ImportPlan, draft: ImportDraft) {
  const secrets = new Set<string>();
  const variables = new Set<string>();
  for (const item of selectedItems(plan, draft)) {
    const references = referencesOf(item.config);
    for (const name of references.secrets) secrets.add(name);
    for (const name of references.variables) variables.add(name);
  }
  return {
    secrets: plan.secrets.filter((secret) => secrets.has(secret.name)),
    variables: plan.variables.filter((variable) => variables.has(variable.name)),
  };
}

/* --------------------------------------------------------------- validation */

export function validateDraft(plan: ImportPlan, draft: ImportDraft): ImportProblems {
  const problems: ImportProblems = { summary: [], items: {}, secrets: {}, variables: {} };
  const chosen = selectedItems(plan, draft);
  if (chosen.length === 0) {
    problems.summary.push(
      plan.items.length === 0
        ? "Nothing in this import can become a check."
        : "Choose at least one check to create.",
    );
  }

  plan.items.forEach((item, position) => {
    if (!isSelected(draft, item)) return;
    const name = (draft.items[item.key]?.name ?? item.name).trim();
    const reason =
      name.length === 0
        ? "Give this check a name."
        : name.length > LIMITS.maxNameLength
          ? `A name may be at most ${LIMITS.maxNameLength} characters.`
          : undefined;
    if (reason) {
      problems.items[item.key] = reason;
      problems.summary.push(`Check ${position + 1}: ${reason}`);
    }
  });

  const inUse = valuesInUse(plan, draft);
  for (const secret of inUse.secrets) {
    const value = draft.secretValues[secret.name] ?? "";
    const reason =
      value.length === 0
        ? "Enter the value for this secret."
        : value.length < MIN_SECRET_LENGTH
          ? `A secret must be at least ${MIN_SECRET_LENGTH} characters.`
          : undefined;
    if (reason) {
      problems.secrets[secret.name] = reason;
      problems.summary.push(`Secret ${secret.name}: ${reason}`);
    }
  }
  for (const variable of inUse.variables) {
    if ((draft.variableValues[variable.name] ?? "").length > 0) continue;
    const reason = "Enter a value for this variable.";
    problems.variables[variable.name] = reason;
    problems.summary.push(`Variable ${variable.name}: ${reason}`);
  }
  return problems;
}

export function hasProblems(problems: ImportProblems): boolean {
  return problems.summary.length > 0;
}

/* ------------------------------------------------------------------- create */

/** What Create sends, and the plan keys in the order the API will number them. */
export function buildConfirmBody(
  plan: ImportPlan,
  draft: ImportDraft,
): { body: ImportConfirmBody; keys: string[] } {
  const chosen = selectedItems(plan, draft);
  const inUse = valuesInUse(plan, draft);
  const typedOrDetected = (values: Record<string, string>, entry: NamedValue): NamedValue => ({
    name: entry.name,
    value: values[entry.name] ?? entry.value,
  });
  return {
    keys: chosen.map((item) => item.key),
    body: {
      checks: chosen.map((item) => ({
        name: (draft.items[item.key]?.name ?? item.name).trim(),
        config: item.config,
        interval_seconds: DEFAULT_INTERVAL_SECONDS,
        locations: [],
      })),
      secrets: inUse.secrets.map((secret) => typedOrDetected(draft.secretValues, secret)),
      variables: inUse.variables.map((variable) => typedOrDetected(draft.variableValues, variable)),
    },
  };
}

export interface ConfirmRefusal {
  /** Reasons by plan item key. */
  items: Record<string, string[]>;
  /** Field problems that name no item: a secret, a variable. */
  general: string[];
}

export function isPlaced(refusal: ConfirmRefusal): boolean {
  return refusal.general.length > 0 || Object.keys(refusal.items).length > 0;
}

/**
 * Where each problem in a refused Create belongs. The API names a check by its position
 * in what was sent (`checks.2.config.request.url`), and `keys` turns that back into the
 * item on screen.
 */
export function placeConfirmRefusal(error: unknown, keys: readonly string[]): ConfirmRefusal {
  const refusal: ConfirmRefusal = { items: {}, general: [] };
  for (const [field, reason] of Object.entries(fieldProblemsOf(error))) {
    const match = CHECK_FIELD.exec(field);
    const key = match ? keys[Number(match[1])] : undefined;
    if (key === undefined) {
      refusal.general.push(`${field}: ${reason}`);
      continue;
    }
    const where = match?.[2] ? `${match[2]}: ` : "";
    refusal.items[key] = [...(refusal.items[key] ?? []), `${where}${reason}`];
  }
  return refusal;
}

/* ----------------------------------------------------------------- sentences */

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** "12 checks · 3 skipped · 2 secrets needed" */
export function planCounts(plan: ImportPlan, draft: ImportDraft): string {
  const chosen = selectedItems(plan, draft).length;
  const secrets = valuesInUse(plan, draft).secrets.length;
  const parts = [
    `${chosen} of ${plural(plan.items.length, "check", "checks")} chosen`,
    `${plan.skipped.length} skipped`,
  ];
  if (secrets > 0) parts.push(`${plural(secrets, "secret", "secrets")} needed`);
  return parts.join(" · ");
}

/** "Created 12 checks from Orders API; 3 skipped" — skipped counts what was not created. */
export function successMessage(plan: ImportPlan, draft: ImportDraft, created: number): string {
  const notChosen = plan.items.length - selectedItems(plan, draft).length;
  const skipped = plan.skipped.length + notChosen;
  return `Created ${plural(created, "check", "checks")} from ${plan.title}; ${skipped} skipped`;
}

const COMPARISON_WORDS: Record<Assertion["comparison"], string> = {
  equals: "equals",
  not_equals: "is not",
  contains: "contains",
  not_contains: "does not contain",
  less_than: "below",
  greater_than: "above",
  matches: "matches",
  is_empty: "is empty",
  is_not_empty: "is not empty",
};

function describeAssertion(assertion: Assertion): string {
  const subject = assertion.property
    ? `${assertion.source.replace("_", " ")} ${assertion.property}`
    : assertion.source.replace("_", " ");
  return `${subject} ${COMPARISON_WORDS[assertion.comparison]} ${assertion.target}`.trim();
}

/** What makes this check fail, in one line of words. */
export function assertionSummary(config: CheckConfig): string {
  const requests = requestsOf(config);
  const first = requests[0];
  if (!first) return "";
  const conditions = first.assertions.map(describeAssertion).join(" and ");
  return requests.length > 1
    ? `${plural(requests.length, "step", "steps")}; each passes when ${conditions}`
    : `passes when ${conditions}`;
}
