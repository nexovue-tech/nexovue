/**
 * What narrows the check list.
 *
 * Both go to the server. "Only what's down" is the one a person opens this
 * screen to ask, and it is also the one a client-side filter would answer worst:
 * the list is capped, and a check that is down is not thereby near the top of it.
 */

import type { FilterDefinition } from "@/shared/components/FilterBar";

export const SYNTHETIC_FILTERS: readonly FilterDefinition[] = [
  {
    key: "check_type",
    label: "Kind",
    control: {
      kind: "select",
      options: [
        { value: "http", label: "HTTP" },
        { value: "browser", label: "Browser" },
      ],
    },
  },
  {
    key: "down_only",
    label: "Only down",
    control: { kind: "toggle", on: "true" },
  },
];
