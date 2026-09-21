import { describe, expect, it } from "vitest";

import type { ApiTokenSummary, TokenStatus } from "./api";
import { orderedTokens } from "./token-order";

const token = (name: string, status: TokenStatus): ApiTokenSummary =>
  ({ name, status }) as ApiTokenSummary;

describe("which token a reader meets first", () => {
  it("puts the ones that still work above the ones that do not", () => {
    // Four revoked above the single live token is what the screen showed.
    const ordered = orderedTokens([
      token("hang-probe", "revoked"),
      token("accept-probe", "revoked"),
      token("my test", "live"),
      token("ci-doc-probe", "revoked"),
    ]);

    expect(ordered.map((each) => each.name)).toEqual([
      "my test",
      "hang-probe",
      "accept-probe",
      "ci-doc-probe",
    ]);
  });

  it("keeps the order the API chose within each half", () => {
    const ordered = orderedTokens([
      token("newer", "live"),
      token("older", "live"),
      token("expired", "expired"),
    ]);

    expect(ordered.map((each) => each.name)).toEqual(["newer", "older", "expired"]);
  });
});
