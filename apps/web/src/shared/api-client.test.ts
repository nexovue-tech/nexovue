// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiClient, isNotFound } from "./api-client";
import { logger } from "./logger";

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

afterEach(async () => {
  // A request left in flight by the test above rejects on its own clock, and its
  // `logger.warn` would land on the next test's spy. Let those settle first.
  await new Promise((resolve) => setTimeout(resolve, 20));
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

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

describe("a request that never got an answer", () => {
  /**
   * The caller's own abort and our timeout both abort the same signal, and the
   * client read only that. So every navigation — React Query cancels a query when
   * its component unmounts — wrote "Request timed out" into the log that is the
   * evidence when a customer says the console was slow.
   */
  function failWith(cause: Error) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw cause;
      }),
    );
  }

  it("calls a caller's cancellation a cancellation, and logs nothing", async () => {
    const warn = vi.spyOn(logger, "warn").mockImplementation(() => {});
    failWith(new DOMException("aborted", "AbortError"));
    const cancel = new AbortController();
    cancel.abort();

    const error = await apiClient
      .get("checks", { signal: cancel.signal })
      .catch((thrown) => thrown);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe("Request cancelled");
    expect((error as ApiError).context.aborted).toBe(true);
    expect((error as ApiError).context.offline).toBe(false);
    expect(warn).not.toHaveBeenCalled();
  });

  it("still calls our own timeout a timeout, and logs it", async () => {
    const warn = vi.spyOn(logger, "warn").mockImplementation(() => {});
    // A server that never answers: the request ends when the signal we passed it
    // aborts, which is what our own timeout does.
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: URL, init?: RequestInit) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("timed out", "TimeoutError")),
            );
          }),
      ),
    );

    const error = await apiClient.get("checks", { timeoutMs: 5 }).catch((thrown) => thrown);

    expect((error as ApiError).message).toBe("Request timed out");
    expect((error as ApiError).context.aborted).toBe(true);
    expect(warn).toHaveBeenCalledWith("Request timed out", expect.anything());
  });

  it("classifies a cancellation that lands while the body is still streaming", async () => {
    // The body is read after `fetch` resolves, so the same unmount can abort here
    // instead. An AbortError escaping raw would not be an ApiError at all, and the
    // query client would treat a navigation as a failure worth a toast.
    const warn = vi.spyOn(logger, "warn").mockImplementation(() => {});
    const cancel = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => {
          cancel.abort();
          throw new DOMException("aborted", "AbortError");
        },
      })),
    );

    const error = await apiClient
      .get("checks", { signal: cancel.signal })
      .catch((thrown) => thrown);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe("Request cancelled");
    expect((error as ApiError).context.aborted).toBe(true);
    expect(warn).not.toHaveBeenCalled();
  });

  it("does not call a genuine network failure a cancellation just because one raced it", async () => {
    // Classification reads the error, not the clock: an unmount that happens while a
    // real failure is propagating must not swallow the evidence of that failure.
    const warn = vi.spyOn(logger, "warn").mockImplementation(() => {});
    const cancel = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        cancel.abort();
        throw new TypeError("Failed to fetch");
      }),
    );

    const error = await apiClient
      .get("checks", { signal: cancel.signal })
      .catch((thrown) => thrown);

    expect((error as ApiError).message).toBe("Could not reach the server");
    expect(warn).toHaveBeenCalledWith("Could not reach the server", expect.anything());
  });

  it("calls an unreachable server unreachable", async () => {
    vi.spyOn(logger, "warn").mockImplementation(() => {});
    failWith(new TypeError("Failed to fetch"));

    const error = await apiClient.get("checks").catch((thrown) => thrown);

    expect((error as ApiError).message).toBe("Could not reach the server");
    expect((error as ApiError).context.offline).toBe(true);
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
