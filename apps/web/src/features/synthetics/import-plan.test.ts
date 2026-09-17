import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api-client";

import type { HttpRequest } from "./check-model";
import type { ImportPlan } from "./import-api";
import {
  assertionSummary,
  buildConfirmBody,
  DEFAULT_INTERVAL_SECONDS,
  draftFromPlan,
  hasProblems,
  placeConfirmRefusal,
  planCounts,
  referencesOf,
  successMessage,
  validateDraft,
  valuesInUse,
  withItem,
  withSecretValue,
  withVariableValue,
} from "./import-plan";

function request(overrides: Partial<HttpRequest>): HttpRequest {
  return {
    name: "",
    method: "GET",
    url: "https://api.example.com/",
    query: [],
    headers: [],
    body: { kind: "none" },
    auth: { kind: "none" },
    assertions: [{ source: "status_code", property: "", comparison: "less_than", target: "400" }],
    extractions: [],
    follow_redirects: true,
    timeout_seconds: 10,
    ...overrides,
  };
}

/** Two checks: one using a bearer secret, one using a header secret and a variable. */
const PLAN: ImportPlan = {
  source: "openapi",
  title: "Orders API",
  servers: [],
  items: [
    {
      key: "GET /orders",
      name: "listOrders",
      method: "GET",
      url: "https://api.example.com/orders",
      config: {
        type: "api",
        request: request({
          url: "https://api.example.com/orders",
          auth: { kind: "bearer", token_secret: "BEARER_AUTH" },
        }),
      },
      needs_values: [],
      needs_files: [],
      selected: true,
    },
    {
      key: "POST /orders/{id}",
      name: "updateOrder",
      method: "POST",
      url: "{{base_url}}/orders/{{id}}",
      config: {
        type: "api",
        request: request({
          method: "POST",
          url: "{{base_url}}/orders/{{id}}",
          headers: [{ name: "X-Api-Key", value: "{{secret.API_KEY}}", enabled: true }],
        }),
      },
      needs_values: ["path parameter id has no example"],
      needs_files: [],
      selected: true,
    },
  ],
  secrets: [
    { name: "BEARER_AUTH", value: "", found_in: "security scheme bearerAuth (bearer token)" },
    { name: "API_KEY", value: "k-found-1", found_in: "the cURL command: X-Api-Key header" },
  ],
  variables: [
    { name: "base_url", value: "https://api.example.com" },
    { name: "id", value: "" },
  ],
  skipped: [{ item: "security scheme oauth", reason: "oauth2 authentication is not supported" }],
};

describe("references", () => {
  it("finds secrets in auth and in {{secret.NAME}}, and variables everywhere else", () => {
    const [first, second] = PLAN.items;
    expect(first && referencesOf(first.config)).toEqual({
      secrets: new Set(["BEARER_AUTH"]),
      variables: new Set(),
    });
    expect(second && referencesOf(second.config)).toEqual({
      secrets: new Set(["API_KEY"]),
      variables: new Set(["base_url", "id"]),
    });
  });

  it("counts only the values a chosen check uses", () => {
    const draft = withItem(draftFromPlan(PLAN), "POST /orders/{id}", { selected: false });
    const inUse = valuesInUse(PLAN, draft);

    expect(inUse.secrets.map((secret) => secret.name)).toEqual(["BEARER_AUTH"]);
    expect(inUse.variables).toEqual([]);
  });
});

describe("validation", () => {
  it("asks for a declared secret, a short secret and an empty variable, by name", () => {
    const draft = withSecretValue(draftFromPlan(PLAN), "API_KEY", "abc");
    const problems = validateDraft(PLAN, draft);

    expect(problems.secrets).toEqual({
      BEARER_AUTH: "Enter the value for this secret.",
      API_KEY: "A secret must be at least 4 characters.",
    });
    expect(problems.variables).toEqual({ id: "Enter a value for this variable." });
    expect(problems.summary).toContain("Secret BEARER_AUTH: Enter the value for this secret.");
  });

  it("passes once every value a chosen check uses is filled in", () => {
    let draft = draftFromPlan(PLAN);
    draft = withSecretValue(draft, "BEARER_AUTH", "tok-typed-1");
    draft = withVariableValue(draft, "id", "ord_1");

    expect(hasProblems(validateDraft(PLAN, draft))).toBe(false);
  });

  it("refuses to create nothing, and an empty name", () => {
    const none = withItem(
      withItem(draftFromPlan(PLAN), "GET /orders", { selected: false }),
      "POST /orders/{id}",
      { selected: false },
    );
    expect(validateDraft(PLAN, none).summary).toEqual(["Choose at least one check to create."]);

    const unnamed = withItem(draftFromPlan(PLAN), "GET /orders", { name: "  " });
    expect(validateDraft(PLAN, unnamed).items["GET /orders"]).toBe("Give this check a name.");
  });
});

describe("the confirm body", () => {
  it("sends only chosen checks, with typed names, and only the values they use", () => {
    let draft = draftFromPlan(PLAN);
    draft = withItem(draft, "POST /orders/{id}", { selected: false });
    draft = withItem(draft, "GET /orders", { name: "  List orders " });
    draft = withSecretValue(draft, "BEARER_AUTH", "tok-typed-1");

    const { body, keys } = buildConfirmBody(PLAN, draft);

    expect(keys).toEqual(["GET /orders"]);
    expect(body.checks).toEqual([
      {
        name: "List orders",
        config: PLAN.items[0]?.config,
        interval_seconds: DEFAULT_INTERVAL_SECONDS,
        locations: [],
      },
    ]);
    expect(body.secrets).toEqual([{ name: "BEARER_AUTH", value: "tok-typed-1" }]);
    expect(body.variables).toEqual([]);
  });

  it("keeps typed values when the plan is read again for another server", () => {
    const typed = withSecretValue(draftFromPlan(PLAN), "BEARER_AUTH", "tok-typed-1");

    expect(draftFromPlan(PLAN, typed).secretValues.BEARER_AUTH).toBe("tok-typed-1");
  });
});

describe("a refused Create", () => {
  const context = { traceId: "t", correlationId: "c", offline: false, aborted: false };

  it("puts each check's problem on the item it was sent as", () => {
    const error = new ApiError("Invalid", 422, context, {
      type: "about:blank",
      title: "Invalid",
      status: 422,
      errors: [
        { field: "body.checks.0.config.request.url", reason: "must be a public address" },
        { field: "secrets.0.value", reason: "too short" },
      ],
    });

    expect(placeConfirmRefusal(error, ["POST /orders/{id}"])).toEqual({
      items: { "POST /orders/{id}": ["config.request.url: must be a public address"] },
      general: ["secrets.0.value: too short"],
    });
  });

  it("places nothing when the refusal names no field", () => {
    const error = new ApiError("Conflict", 409, context, {
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "This project already has 100 checks, which is the limit.",
    });

    expect(placeConfirmRefusal(error, ["GET /orders"])).toEqual({ items: {}, general: [] });
  });
});

describe("sentences", () => {
  it("counts what is chosen, skipped and needed", () => {
    expect(planCounts(PLAN, draftFromPlan(PLAN))).toBe(
      "2 of 2 checks chosen · 1 skipped · 2 secrets needed",
    );
  });

  it("says what was created from where, counting unchosen checks as skipped", () => {
    const draft = withItem(draftFromPlan(PLAN), "POST /orders/{id}", { selected: false });

    expect(successMessage(PLAN, draft, 1)).toBe("Created 1 check from Orders API; 2 skipped");
  });

  it("summarises what makes a check fail", () => {
    const first = PLAN.items[0];
    expect(first && assertionSummary(first.config)).toBe("passes when status code below 400");
  });
});
