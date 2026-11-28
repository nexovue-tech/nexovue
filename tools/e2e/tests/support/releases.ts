/**
 * Traffic that carries a version, for the releases screens (F28).
 *
 * The version rollup counts SERVER spans by `service.version` before sampling, so counts here
 * are exact whatever the sampler keeps. Spans are placed whole minutes in the past, so the
 * rollup's settled minute has passed them and the sync job can see each version.
 */

import { createHmac, randomBytes } from "node:crypto";

import { type APIRequestContext, expect } from "@playwright/test";

const NS_PER_MS = 1_000_000;
const MS_PER_MINUTE = 60_000;
const SPAN_KIND_SERVER = 2;
const STATUS_CODE_OK = 1;
const STATUS_CODE_ERROR = 2;
const SPAN_DURATION_MS = 40;

export interface VersionLoad {
  service: string;
  version: string;
  /** Whole minutes before now that the traffic is placed in. */
  minutesAgo: number;
  requests: number;
  failures: number;
}

/** Each request is its own single-span trace, as the rollup counts requests. */
export function versionBatch(load: VersionLoad) {
  // The middle of the minute, so a slow machine cannot push it across the boundary.
  const minuteStart = Math.floor(Date.now() / MS_PER_MINUTE) * MS_PER_MINUTE;
  const startedAtMs = minuteStart - load.minutesAgo * MS_PER_MINUTE + MS_PER_MINUTE / 2;
  const spans = Array.from({ length: load.requests }, (_, index) => ({
    traceId: randomBytes(16).toString("base64"),
    spanId: randomBytes(8).toString("base64"),
    name: "GET /items",
    kind: SPAN_KIND_SERVER,
    startTimeUnixNano: String(startedAtMs * NS_PER_MS),
    endTimeUnixNano: String((startedAtMs + SPAN_DURATION_MS) * NS_PER_MS),
    status: { code: index < load.failures ? STATUS_CODE_ERROR : STATUS_CODE_OK },
  }));
  return {
    resourceSpans: [
      {
        resource: {
          attributes: [
            { key: "service.name", value: { stringValue: load.service } },
            { key: "service.version", value: { stringValue: load.version } },
          ],
        },
        scopeSpans: [{ scope: { name: "nexovue-e2e" }, spans }],
      },
    ],
  };
}

export async function sendVersionLoad(
  request: APIRequestContext,
  credentials: { endpoint: string; apiKey: string },
  load: VersionLoad,
): Promise<void> {
  const response = await request.post(new URL("/v1/traces", credentials.endpoint).toString(), {
    headers: { "content-type": "application/json", "x-nexovue-key": credentials.apiKey },
    data: versionBatch(load),
  });
  expect(response.status(), `receiver refused the batch: ${await response.text()}`).toBe(200);
}

/** A deploy the way the setup page's curl recipe sends it: the body signed with the secret. */
export async function sendSignedDeploy(
  request: APIRequestContext,
  options: {
    webhookUrl: string;
    secret: string;
    service: string;
    version: string;
    deliveryId: string;
  },
): Promise<number> {
  const body = JSON.stringify({
    deploys: [
      {
        delivery_id: options.deliveryId,
        service_name: options.service,
        version: options.version,
        deployed_at: new Date().toISOString(),
      },
    ],
  });
  const signature = createHmac("sha256", options.secret).update(body).digest("hex");
  const response = await request.post(options.webhookUrl, {
    headers: { "content-type": "application/json", "x-hub-signature-256": `sha256=${signature}` },
    data: body,
  });
  return response.status();
}
