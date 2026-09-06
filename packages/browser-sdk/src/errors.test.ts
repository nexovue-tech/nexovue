// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetDiagnostics } from "./diagnostics";
import type { CapturedError } from "./errors";
import { captureErrors } from "./errors";

let captured: CapturedError[];
let detach: () => void;

beforeEach(() => {
  resetDiagnostics();
  captured = [];
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  detach = captureErrors((error) => captured.push(error));
});

afterEach(() => {
  detach();
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

function throwAt(init: ErrorEventInit) {
  window.dispatchEvent(new ErrorEvent("error", init));
}

function reject(reason: unknown) {
  const event = new Event("unhandledrejection");
  Object.defineProperty(event, "reason", { value: reason });
  window.dispatchEvent(event);
}

describe("uncaught errors", () => {
  it("sends the raw minified stack, untouched", () => {
    const error = new TypeError("t is not a function");
    error.stack = "TypeError: t is not a function\n    at n (https://cdn.test/a.min.js:1:2145)";
    throwAt({ message: "Uncaught TypeError", error });

    expect(captured).toHaveLength(1);
    expect(captured[0]).toMatchObject({
      type: "TypeError",
      message: "t is not a function",
      stacktrace: error.stack,
    });
  });

  it("falls back to the event's own location when there is no Error object", () => {
    throwAt({
      message: "boom",
      filename: "https://shop.test/app.js",
      lineno: 12,
      colno: 5,
      error: null,
    });

    expect(captured[0]).toMatchObject({
      type: "Error",
      message: "boom",
      stacktrace: undefined,
      filePath: "https://shop.test/app.js",
      line: 12,
      column: 5,
    });
  });

  it("tells the customer how to unmute a cross-origin script's errors", () => {
    throwAt({ message: "Script error.", filename: "", lineno: 0, colno: 0, error: null });

    const said = vi.mocked(console.error).mock.calls.map(String).join(" ");
    expect(said).toContain('crossorigin="anonymous"');
    expect(said).toContain("Access-Control-Allow-Origin");
    expect(captured).toHaveLength(1);
  });

  it("ignores a resource load failure, which is not an exception", () => {
    const image = document.createElement("img");
    document.body.appendChild(image);
    image.dispatchEvent(new Event("error"));

    expect(captured).toEqual([]);
  });

  it("never assigns window.onerror, which would replace the customer's own handler", () => {
    expect(window.onerror).toBeNull();
  });
});

describe("unhandled rejections", () => {
  it("reads an Error reason like any other throw", () => {
    reject(new RangeError("out of range"));
    expect(captured[0]).toMatchObject({ type: "RangeError", message: "out of range" });
  });

  it("names a thrown string by its constructor rather than dropping it", () => {
    reject("plain string reason");
    expect(captured[0]).toMatchObject({
      type: "String",
      message: "plain string reason",
      stacktrace: undefined,
    });
  });

  it("still supplies a type for a null reason, or the Errors screen would not show it", () => {
    reject(null);
    expect(captured[0]?.type).toBe("Error");
  });

  it("describes a plain object reason without reading .stack off it", () => {
    reject({ status: 503, detail: "upstream" });
    expect(captured[0]).toMatchObject({
      type: "Object",
      message: '{"status":503,"detail":"upstream"}',
    });
  });

  it("survives a reason that throws when it is inspected", () => {
    const hostile = new Proxy(
      {},
      {
        get() {
          throw new Error("no");
        },
      },
    );
    expect(() => reject(hostile)).not.toThrow();
    expect(captured[0]?.type).toBe("Error");
  });

  it("survives a circular reason that cannot be stringified", () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(() => reject(circular)).not.toThrow();
    expect(captured).toHaveLength(1);
  });
});
