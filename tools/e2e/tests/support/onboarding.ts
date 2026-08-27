/**
 * Reading the onboarding screen the way a customer does.
 *
 * The two lines it shows are the only place the ingest key ever exists in plaintext, so
 * the suite takes them off the screen rather than from the API. That way the export it
 * then performs is the export the product just told someone to perform: a wrong endpoint
 * or an unusable key fails the test instead of being quietly bypassed.
 */

import type { Page } from "@playwright/test";

const ENDPOINT_LINE = /^OTEL_EXPORTER_OTLP_ENDPOINT=(\S+)$/;
const HEADERS_LINE = /^OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=(\S+)$/;

export interface ExportSettings {
  endpoint: string;
  apiKey: string;
}

export async function readExportSettings(page: Page): Promise<ExportSettings> {
  return {
    endpoint: await readVariable(page, ENDPOINT_LINE),
    apiKey: await readVariable(page, HEADERS_LINE),
  };
}

async function readVariable(page: Page, pattern: RegExp): Promise<string> {
  const line = (await page.getByText(pattern).innerText()).trim();
  const value = pattern.exec(line)?.[1];
  if (value === undefined) throw new Error(`onboarding showed no value in "${line}"`);
  return value;
}
