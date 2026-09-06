import type { ResolvedConfig } from "../config";
import { SDK_NAME, SDK_VERSION } from "../constants";
import {
  ATTR_BROWSER_BRANDS,
  ATTR_BROWSER_LANGUAGE,
  ATTR_BROWSER_MOBILE,
  ATTR_BROWSER_PLATFORM,
  ATTR_SERVICE_NAME,
  ATTR_SERVICE_VERSION,
  ATTR_TELEMETRY_SDK_LANGUAGE,
  ATTR_TELEMETRY_SDK_NAME,
  ATTR_TELEMETRY_SDK_VERSION,
  ATTR_USER_AGENT_ORIGINAL,
} from "../semconv";
import { AttributeList, type KeyValue } from "./values";

/**
 * User-Agent Client Hints. Chromium only — Firefox and Safari expose nothing —
 * and the three low-entropy fields read below are synchronous and need no
 * permission. Where they are absent they are left unset, never filled with "".
 */
interface UserAgentBrand {
  brand: string;
  version: string;
}
interface UserAgentData {
  brands?: UserAgentBrand[];
  platform?: string;
  mobile?: boolean;
}

/**
 * The resource every export carries. user_agent.original is the load-bearing
 * one: it is Stable, and it is the only device fact present on 100% of traffic.
 */
export function browserResource(config: ResolvedConfig): KeyValue[] {
  const attributes = new AttributeList()
    .string(ATTR_SERVICE_NAME, config.serviceName)
    .string(ATTR_SERVICE_VERSION, config.serviceVersion)
    .string(ATTR_TELEMETRY_SDK_NAME, SDK_NAME)
    .string(ATTR_TELEMETRY_SDK_LANGUAGE, "webjs")
    .string(ATTR_TELEMETRY_SDK_VERSION, SDK_VERSION)
    .string(ATTR_USER_AGENT_ORIGINAL, navigator.userAgent)
    .string(ATTR_BROWSER_LANGUAGE, navigator.language);

  const hints = (navigator as Navigator & { userAgentData?: UserAgentData }).userAgentData;
  if (hints) {
    attributes
      .strings(
        ATTR_BROWSER_BRANDS,
        hints.brands?.map((brand) => `${brand.brand} ${brand.version}`),
      )
      .string(ATTR_BROWSER_PLATFORM, hints.platform)
      .bool(ATTR_BROWSER_MOBILE, hints.mobile);
  }

  return attributes.build();
}
