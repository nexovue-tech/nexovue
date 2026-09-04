// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiClient, isNotFound } from "./api-client";

/**
 * The regression these guard: `ApiError` was constructed without its fourth argument,
 * so `error.problem` was `undefined` for every HTTP failure in the app. Nothing threw —
 * the error screen, the 5xx toast and every inline field message simply went blank.
 */

function respondWith(status: number, body: unknown, contentType = "application/problem+json") {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(body), { status, headers: { "content-type": contentType } }),
    ),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("apiClient failures", () => {
  it("carries the parsed problem body onto the error", async () => {
    respondWith(422, {
      type: "https://nexovue.dev/problems/validation",
      title: "That did not validate",
      status: 422,
      detail: "Check the fields below.",
    });

    const error = await apiClient.get("traces").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).problem).toBeDefined();
    expect((error as ApiError).problem?.detail).toBe("Check the fields below.");
    expect((error as ApiError).status).toBe(422);
  });

  it("keeps problem extension members, which is where field errors live", async () => {
    respondWith(422, {
      title: "Invalid",
      status: 422,
      errors: [{ field: "body.email", reason: "already taken" }],
    });

    const error = (await apiClient.post("signup", {}).catch((c: unknown) => c)) as ApiError;
    expect(error.problem?.errors).toEqual([{ field: "body.email", reason: "already taken" }]);
  });

  it("titles the error from the problem body rather than the bare status text", async () => {
    respondWith(500, { title: "The database is unreachable", status: 500 });
    const error = (await apiClient.get("traces").catch((c: unknown) => c)) as ApiError;
    expect(error.message).toBe("The database is unreachable");
  });

  it("still fails usefully when the body is not a problem document", async () => {
    respondWith(503, "gateway down", "text/plain");
    const error = (await apiClient.get("traces").catch((c: unknown) => c)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(503);
    expect(error.problem).toBeUndefined();
  });
});

describe("isNotFound", () => {
  it("recognises the API's answer that no such resource exists here", async () => {
    respondWith(404, { title: "No such resource in this organization", status: 404 });
    const error = await apiClient.get("traces/abc").catch((c: unknown) => c);
    expect(isNotFound(error)).toBe(true);
  });

  it("is false for a failure, so an unreachable server never reads as an absent row", async () => {
    respondWith(500, { title: "The database is unreachable", status: 500 });
    const serverError = await apiClient.get("traces/abc").catch((c: unknown) => c);
    expect(isNotFound(serverError)).toBe(false);

    expect(isNotFound(new Error("Could not reach the server"))).toBe(false);
    expect(isNotFound(undefined)).toBe(false);
  });
});
