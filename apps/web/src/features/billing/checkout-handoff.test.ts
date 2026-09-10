// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  CheckoutUnavailableError,
  loadScript,
  openCheckout,
  SCRIPT_INCOMPLETE,
  widgetFrom,
} from "./checkout-handoff";

/**
 * A document whose script tags never really fetch: appending one fires the
 * outcome this test asked for, on a later tick.
 */
function documentThatServes(outcome: "load" | "error"): Document {
  const head = { appendChild: (tag: HTMLScriptElement) => queueMicrotask(() => fire(tag)) };
  const fire = (tag: HTMLScriptElement) => tag.dispatchEvent(new Event(outcome));
  return {
    createElement: () => document.createElement("script"),
    head,
  } as unknown as Document;
}

describe("loading the processor's script", () => {
  it("resolves once the browser has run it", async () => {
    await expect(
      loadScript(`https://p.test/${crypto.randomUUID()}.js`, documentThatServes("load")),
    ).resolves.toBeUndefined();
  });

  it("fetches one script once however many checkouts are started", async () => {
    const url = `https://p.test/${crypto.randomUUID()}.js`;
    let appended = 0;
    const counting = {
      createElement: () => document.createElement("script"),
      head: {
        appendChild: (tag: HTMLScriptElement) => {
          appended += 1;
          queueMicrotask(() => tag.dispatchEvent(new Event("load")));
        },
      },
    } as unknown as Document;

    await Promise.all([loadScript(url, counting), loadScript(url, counting)]);
    await loadScript(url, counting);

    expect(appended).toBe(1);
  });

  /**
   * A blocked request the customer then unblocks has to be retryable without a
   * reload — so the rejection is not what the next caller gets handed.
   */
  it("does not remember a failure", async () => {
    const url = `https://p.test/${crypto.randomUUID()}.js`;

    await expect(loadScript(url, documentThatServes("error"))).rejects.toBeInstanceOf(
      CheckoutUnavailableError,
    );

    await expect(loadScript(url, documentThatServes("load"))).resolves.toBeUndefined();
  });
});

describe("finding what the script defined", () => {
  it("takes the constructor the API named", () => {
    const Widget = (() => {}) as unknown as () => void;

    expect(widgetFrom({ SomeProcessor: Widget }, "SomeProcessor")).toBe(Widget);
  });

  /** Otherwise the customer meets a blank page at the moment they meant to pay. */
  it("refuses a script that loaded and defined nothing", () => {
    expect(() => widgetFrom({}, "SomeProcessor")).toThrow(SCRIPT_INCOMPLETE);
  });
});

describe("opening the checkout", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("navigates for a processor that owns a whole page", async () => {
    const window = { location: { href: "" } } as unknown as Window & typeof globalThis;

    await openCheckout({ style: "hosted_page", url: "https://p.test/pay/abc" }, window);

    expect(window.location.href).toBe("https://p.test/pay/abc");
  });

  it("constructs and opens for a processor that owns a script", async () => {
    const opened = vi.fn();
    const seen: Record<string, unknown>[] = [];
    const url = `https://p.test/${crypto.randomUUID()}.js`;
    const scope: Record<string, unknown> = {
      SomeProcessor: function (this: unknown, options: Record<string, unknown>) {
        seen.push(options);
        return { open: opened };
      },
      document: documentThatServes("load"),
      location: { href: "" },
    };
    // The constructor above returns an object, which `new` honours.
    const window = scope as unknown as Window & typeof globalThis;

    await openCheckout(
      {
        style: "embedded",
        script_url: url,
        global_name: "SomeProcessor",
        options: { subscription_id: "sub_1", callback_url: "https://api.test/return" },
      },
      window,
    );

    expect(opened).toHaveBeenCalledOnce();
    expect(seen).toEqual([{ subscription_id: "sub_1", callback_url: "https://api.test/return" }]);
    // Not navigated away: the whole point is that the customer stays here.
    expect((window as unknown as { location: { href: string } }).location.href).toBe("");
  });

  it("reports a script that will not load rather than doing nothing", async () => {
    const window = {
      document: documentThatServes("error"),
      location: { href: "" },
    } as unknown as Window & typeof globalThis;

    await expect(
      openCheckout(
        {
          style: "embedded",
          script_url: `https://p.test/${crypto.randomUUID()}.js`,
          global_name: "SomeProcessor",
          options: {},
        },
        window,
      ),
    ).rejects.toBeInstanceOf(CheckoutUnavailableError);
  });
});
