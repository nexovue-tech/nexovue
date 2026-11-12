import { ApiError } from "@/shared/api-client";
import { visibleEscapes } from "@/shared/format/visible-escapes";

import type { DashboardDocument } from "./api";

/**
 * Export and import (F20, FR42, D22, D53). A file is the dashboard document alone: no
 * organization, owner, dashboard id, row id or version, so it works in another project or
 * organization and says nothing about this one. Checks are named and a metric series is
 * its hash, so it holds no attribute values.
 *
 * Import is untrusted: the file is read here, refused past 64 KiB or when it is not JSON,
 * and otherwise sent to `POST /dashboards/validate`, which runs the save's own checks with
 * no read and no write. Only the document the API passed back opens, as an unsaved draft;
 * the file's own text never does.
 */

/** The API's cap on a dashboard (`definition.MAX_DOCUMENT_BYTES`), for the file too (AC139). */
export const MAX_FILE_BYTES = 64 * 1024;
const BYTES_PER_KIB = 1024;
export const EXPORT_TYPE = "application/json";
/** FR62, AC119: said wherever a file can be made. */
export const EXPORT_WARNING = "This file includes notes, links, service names and host names.";
/** Read from the API's words, so the console never says "should be 1" about a format. */
const VALUE_ERROR_PREFIX = "Value error, ";

type Documented = Pick<DashboardDocument, "title" | "description" | "definition">;

/** What a file holds: the document, and nothing that names where it came from (FR42). */
export function exportedDocument(dashboard: Documented): DashboardDocument {
  return {
    title: dashboard.title,
    description: dashboard.description,
    definition: dashboard.definition,
  };
}

/** The file's text, indented so a person can read and compare two of them. */
export function exportText(dashboard: Documented): string {
  return JSON.stringify(exportedDocument(dashboard), null, 2);
}

/** A file name from the title, safe on every system: anything else becomes a hyphen. */
export function exportFileName(title: string): string {
  return `${title.replace(/[^\w.-]+/g, "-") || "dashboard"}.json`;
}

/** Hands the browser the file to save. Export writes no audit entry (FR27). */
export function downloadDashboard(dashboard: Documented): void {
  const url = URL.createObjectURL(new Blob([exportText(dashboard)], { type: EXPORT_TYPE }));
  const link = document.createElement("a");
  link.href = url;
  link.download = exportFileName(dashboard.title);
  link.click();
  URL.revokeObjectURL(url);
}

/** A chosen file, read: its parsed contents for the API to check, or why it was not sent. */
export type FileReading =
  | { kind: "read"; document: unknown }
  | { kind: "refused"; reasons: string[] };

/** Reads a chosen file in the browser. Nothing past 64 KiB, or that is not JSON, is sent. */
export async function readDashboardFile(file: Blob): Promise<FileReading> {
  if (file.size > MAX_FILE_BYTES) {
    const kib = Math.ceil(file.size / BYTES_PER_KIB);
    return {
      kind: "refused",
      reasons: [`This file is ${kib} KiB, and a dashboard file is at most 64 KiB.`],
    };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(await file.text());
  } catch {
    return { kind: "refused", reasons: ["This file is not a dashboard file: it is not JSON."] };
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { kind: "refused", reasons: ["This file is not a dashboard file."] };
  }
  return { kind: "read", document: parsed };
}

const FIELD_NAMES: readonly [RegExp, (match: RegExpExecArray) => string][] = [
  [/^body\.title$/, () => "Title"],
  [/^body\.description$/, () => "Description"],
  [/^body\.definition\.schema_version$/, () => "Format"],
  [/^body\.definition\.links\.(\d+)\.url$/, (match) => `Link ${Number(match[1]) + 1} address`],
  [/^body\.definition\.links\.(\d+)\.label$/, (match) => `Link ${Number(match[1]) + 1} label`],
  [/^body\.definition\.panels\.(\d+)/, (match) => `Panel ${Number(match[1]) + 1}`],
];

/** A field path from the API, in the words the screen uses for it. */
function fieldName(field: string): string {
  for (const [pattern, name] of FIELD_NAMES) {
    const match = pattern.exec(field);
    if (match) return name(match);
  }
  return "The dashboard";
}

/**
 * What the API refused, one sentence each, never the submitted values: the same words for
 * a save and for a file. A refusal with no field list is its one sentence.
 */
export function refusalWords(error: unknown, fallback: string): string[] {
  if (!(error instanceof ApiError)) return [fallback];
  const errors = error.problem?.errors;
  if (Array.isArray(errors) && errors.length > 0) {
    return (errors as { field?: string; reason?: string }[]).map((each) => {
      const reason = (each.reason ?? "refused").replace(VALUE_ERROR_PREFIX, "");
      return `${fieldName(each.field ?? "")}: ${visibleEscapes(reason)}`;
    });
  }
  return [error.problem?.detail ?? fallback];
}
