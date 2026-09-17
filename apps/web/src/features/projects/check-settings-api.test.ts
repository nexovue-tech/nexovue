import { describe, expect, it } from "vitest";

import {
  byName,
  isTaken,
  MAX_SECRET_LENGTH,
  MAX_VARIABLE_LENGTH,
  secretNameProblem,
  secretReference,
  secretValueProblem,
  variableNameProblem,
  variableReference,
  variableValueProblem,
} from "./check-settings-api";

describe("how a check refers to a secret and a variable", () => {
  it("writes the two syntaxes the scheduler substitutes", () => {
    expect(secretReference("API_TOKEN")).toBe("{{secret.API_TOKEN}}");
    expect(variableReference("order.id")).toBe("{{order.id}}");
  });
});

describe("secret names", () => {
  it("accepts the API's pattern", () => {
    expect(secretNameProblem("API_TOKEN")).toBeUndefined();
    expect(secretNameProblem("_private")).toBeUndefined();
    expect(secretNameProblem(`A${"b".repeat(63)}`)).toBeUndefined();
  });

  it("refuses what a variable may have but a secret may not", () => {
    // A secret name is plain so it reads the same in this list and in a request.
    expect(secretNameProblem("api.token")).toBeDefined();
    expect(secretNameProblem("api-token")).toBeDefined();
  });

  it("refuses a leading digit and a 65th character", () => {
    expect(secretNameProblem("1token")).toBeDefined();
    expect(secretNameProblem(`A${"b".repeat(64)}`)).toBeDefined();
  });

  it("says nothing about an empty field, which is unfinished rather than wrong", () => {
    expect(secretNameProblem("")).toBeUndefined();
  });
});

describe("variable names", () => {
  it("accepts dots and hyphens, because Postman's names have them", () => {
    expect(variableNameProblem("order.id")).toBeUndefined();
    expect(variableNameProblem("base-url")).toBeUndefined();
  });

  it("refuses a space and a leading hyphen", () => {
    expect(variableNameProblem("base url")).toBeDefined();
    expect(variableNameProblem("-base")).toBeDefined();
  });
});

describe("values", () => {
  it("refuses a secret short enough to blank ordinary text when redacted", () => {
    expect(secretValueProblem("abc")).toBeDefined();
    expect(secretValueProblem("abcd")).toBeUndefined();
  });

  it("refuses a secret or variable past the API's length", () => {
    expect(secretValueProblem("x".repeat(MAX_SECRET_LENGTH + 1))).toBeDefined();
    expect(variableValueProblem("x".repeat(MAX_VARIABLE_LENGTH + 1))).toBeDefined();
    expect(variableValueProblem("x".repeat(MAX_VARIABLE_LENGTH))).toBeUndefined();
  });

  it("allows an empty variable, which the API stores", () => {
    expect(variableValueProblem("")).toBeUndefined();
  });
});

describe("the list helpers", () => {
  it("knows a taken name exactly, not case-insensitively", () => {
    const existing = [{ name: "TOKEN" }];

    expect(isTaken("TOKEN", existing)).toBe(true);
    expect(isTaken("token", existing)).toBe(false);
  });

  it("orders by name without reordering what it was given", () => {
    const given = [{ name: "b" }, { name: "a" }];

    expect(byName(given).map((item) => item.name)).toEqual(["a", "b"]);
    expect(given.map((item) => item.name)).toEqual(["b", "a"]);
  });
});
