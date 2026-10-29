import type { ReactNode } from "react";

import { useSystemInfo } from "@/shared/deployment";

/**
 * A screen only nexovue's hosted service has — Billing, Support access. On a customer's own
 * install the address answers as any address with no screen does, not with the screen's own
 * failure to load (design.md §4; found by the design check, 2026-10-29).
 *
 * Nothing until the answer is in, so neither a billing screen nor a 404 flashes first.
 */
export function HostedOnly({ children, fallback }: { children: ReactNode; fallback: ReactNode }) {
  const info = useSystemInfo();
  if (info.isPending) return null;
  return info.data?.deployment === "hosted" ? children : fallback;
}
