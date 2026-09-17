import { describe, expect, it } from "vitest";

import {
  attachedFileLabel,
  configFrom,
  draftFrom,
  emptyDraft,
  emptyRow,
  inputFrom,
  problemsFromApi,
  problemsUnder,
  rowProblems,
  sameCheck,
  suggestedName,
  validate,
  withBodyKind,
} from "./check-draft";
import type { CheckConfig } from "./check-model";

const stored: CheckConfig = {
  type: "api",
  request: {
    name: "",
    method: "POST",
    url: "https://api.example.com/orders/{{order_id}}",
    query: [{ name: "page", value: "2", enabled: true }],
    headers: [{ name: "X-Trace", value: "abc", enabled: false }],
    body: { kind: "json", content: '{"qty": 2}' },
    auth: { kind: "bearer", token_secret: "TOKEN" },
    assertions: [{ source: "json_path", property: "$.id", comparison: "is_not_empty", target: "" }],
    extractions: [],
    follow_redirects: false,
    timeout_seconds: 12,
  },
};

const check = {
  name: "Create order",
  config: stored,
  interval_seconds: 300,
  locations: ["mumbai"],
  degraded_ms: 800,
  failing_ms: null,
  retries: 2,
};

describe("a stored check survives the round trip through the form", () => {
  it("returns the same input it was built from", () => {
    const draft = draftFrom(check);
    expect(inputFrom(draft)).toEqual(check);
  });

  it("is not dirty until something that would be saved differs", () => {
    const draft = draftFrom(check);
    const touched = {
      ...draft,
      request: { ...draft.request, headers: [...draft.request.headers, emptyRow()] },
    };
    expect(sameCheck(draft, touched)).toBe(true);
    expect(sameCheck(draft, { ...draft, degradedMs: "900" })).toBe(false);
  });
});

describe("a body's content type follows its kind", () => {
  const withFile = (kind: "binary" | "text") => {
    const draft = emptyDraft("api");
    draft.request.body = { ...withBodyKind(draft.request.body, kind), fileId: "file-1" };
    return draft;
  };

  it("sends a binary body as octet-stream, not as text/plain", () => {
    const draft = withFile("binary");
    expect(draft.request.body.contentType).toBe("application/octet-stream");
    expect(configFrom(draft)).toMatchObject({
      request: {
        body: { kind: "binary", file_id: "file-1", content_type: "application/octet-stream" },
      },
    });
  });

  it("swaps one kind's default for the other's, and keeps a type someone typed", () => {
    const text = withFile("text").request.body;
    expect(text.contentType).toBe("text/plain");
    expect(withBodyKind(text, "binary").contentType).toBe("application/octet-stream");
    expect(withBodyKind(withBodyKind(text, "json"), "text").contentType).toBe("text/plain");
    expect(withBodyKind({ ...text, contentType: "text/csv" }, "binary").contentType).toBe(
      "text/csv",
    );
  });

  it("names a stored file by its id, since the definition keeps no file name", () => {
    const draft = draftFrom({
      ...check,
      config: {
        type: "api",
        request: {
          ...stored.request,
          body: {
            kind: "binary",
            file_id: "0f8e2c1a-5b7d-4e3f-9a1b-2c3d4e5f6a7b",
            content_type: "image/png",
          },
        },
      },
    });
    expect(draft.request.body.contentType).toBe("image/png");
    expect(attachedFileLabel(draft.request.body.fileId, draft.request.body.fileName)).toBe(
      "attached file (0f8e2c1a)",
    );
    expect(attachedFileLabel("0f8e2c1a", "logo.png")).toBe("logo.png");
  });
});

describe("validation names the field the API would name", () => {
  it("wants a name on every query, header and form row that carries a value", () => {
    const draft = emptyDraft("api");
    draft.request.url = "https://a.example";
    draft.request.query = [emptyRow(), { ...emptyRow(), value: "2" }];
    draft.request.headers = [{ ...emptyRow(), value: "abc" }];
    draft.request.body = {
      ...withBodyKind(draft.request.body, "form"),
      fields: [{ ...emptyRow(), value: "x" }],
    };
    expect(validate(draft)).toEqual({
      "config.request.query.1.name": "Name the parameter.",
      "config.request.headers.0.name": "Name the header.",
      "config.request.body.fields.0.name": "Name the field.",
    });
  });

  it("refuses a check that asserts nothing", () => {
    const draft = emptyDraft("api");
    draft.request.url = "https://a.example";
    draft.request.assertions = [];
    expect(validate(draft)["config.request.assertions"]).toMatch(/at least one assertion/);
  });

  it("keeps a multistep run inside its time limit", () => {
    const draft = emptyDraft("multistep");
    for (const step of draft.steps) {
      step.url = "https://a.example";
      step.timeoutSeconds = "30";
    }
    draft.steps.push({ ...draft.steps[0], key: "third" });
    expect(validate(draft)["config.steps"]).toMatch(/90s; at most 60s/);
  });

  it("wants numbers where the comparison is a number", () => {
    const draft = emptyDraft("api");
    draft.request.url = "https://a.example";
    draft.request.assertions[0] = { ...draft.request.assertions[0], target: "soon" };
    expect(validate(draft)["config.request.assertions.0.target"]).toBe("A number.");
  });

  it("puts failing above degraded", () => {
    const draft = emptyDraft("tcp");
    Object.assign(draft, { host: "db.example.com", degradedMs: "500", failingMs: "200" });
    expect(validate(draft).failing_ms).toBe("Must be above the degraded threshold.");
  });

  it("names an unknown reference on the field that wrote it, not on the request", () => {
    const draft = emptyDraft("api");
    draft.request.url = "https://a.example/{{tenant}}";
    draft.request.headers = [{ ...emptyRow(), name: "X-Key", value: "{{secret.MISSING}}" }];
    draft.request.body = { ...withBodyKind(draft.request.body, "json"), text: '{"id":"{{run}}"}' };

    const problems = validate(draft, { secrets: ["KNOWN"], variables: ["tenant"] });

    // `tenant` is stored, so the URL is fine; the other two are named where they are.
    expect(problems["config.request.url"]).toBeUndefined();
    expect(problems["config.request.headers.0.value"]).toMatch(/no secret named MISSING/);
    expect(problems["config.request.body.content"]).toMatch(/no variable named run/);
    // Without the project's names nothing is claimed: the API is still the authority.
    expect(validate(draft)["config.request.body.content"]).toBeUndefined();
  });

  it("counts an earlier request's extraction as defined for the ones after it", () => {
    const draft = emptyDraft("multistep");
    draft.steps[0].url = "https://a.example/login";
    draft.steps[0].extractions = [
      { key: "k", name: "token", source: "json_path", expression: "$.token" },
    ];
    draft.steps[1].url = "https://a.example/orders?t={{token}}";

    const problems = validate(draft, { secrets: [], variables: [] });

    expect(problems["config.steps.1.url"]).toBeUndefined();
    // The other way round it is undefined: request 1 cannot use request 2's value.
    draft.steps[0].url = "https://a.example/login?t={{token}}";
    draft.steps[0].extractions = [];
    draft.steps[1].extractions = [
      { key: "k", name: "token", source: "json_path", expression: "$.token" },
    ];
    expect(validate(draft, { secrets: [], variables: [] })["config.steps.0.url"]).toMatch(
      /no variable named token/,
    );
  });

  it("accepts a heartbeat with no thresholds and no locations", () => {
    const draft = emptyDraft("heartbeat");
    expect(validate(draft)).toEqual({});
    expect(inputFrom(draft)).toMatchObject({ locations: [], degraded_ms: null, failing_ms: null });
  });
});

describe("API problems land under the form's paths", () => {
  it("drops the discriminator segments Pydantic adds", () => {
    const problems = problemsFromApi(
      {
        "config.api.request.body.json.content": "too long",
        "config.multistep.steps.1.auth.bearer.token_secret": "String should match pattern",
        "config.request": "Value error, no secret named TOKEN in this project",
      },
      emptyDraft("api"),
    );
    expect(problems).toEqual({
      "config.request.body.content": "too long",
      "config.steps.1.auth.token_secret": "String should match pattern",
      "config.request": "no secret named TOKEN in this project",
    });
    expect(problemsUnder(problems, "config.request")).toHaveLength(2);
  });

  it("maps a sent row's number back to its row on screen, past the unused rows left out", () => {
    const draft = emptyDraft("multistep");
    const second = draft.steps[1] ?? emptyDraft("api").request;
    second.headers = [
      emptyRow(),
      { ...emptyRow(), name: "X-A" },
      emptyRow(),
      { ...emptyRow(), name: "X-B" },
    ];
    const problems = problemsFromApi(
      {
        "config.multistep.steps.1.headers.1.value": "must not contain line breaks",
        "config.multistep.steps.1.headers.0": "duplicate header",
      },
      draft,
    );
    expect(problems).toEqual({
      "config.steps.1.headers.3.value": "must not contain line breaks",
      "config.steps.1.headers.1": "duplicate header",
    });
    expect(rowProblems(problems, "config.steps.1.headers")).toEqual({ 1: "duplicate header" });
  });
});

describe("a check with no name is named after what it calls", () => {
  it("uses the method and the URL without its scheme", () => {
    const draft = emptyDraft("api");
    draft.request.url = "https://shop.example.com/health";
    expect(suggestedName(draft)).toBe("GET shop.example.com/health");
    expect(configFrom(draft).type).toBe("api");
  });
});
